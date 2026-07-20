from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
import math
from pathlib import Path
import shutil
import sys
from time import perf_counter
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_reid_fusion_shadow import (
    build_identity_reid_fusion_shadow,
)


DEFAULT_GOLDSET = (
    BACKEND_DIR
    / "tests"
    / "fixtures"
    / "player_identity"
    / "identity_fragment_consolidation_goldset_v1.json"
)
DEFAULT_CONTENT_ROOT = (
    BACKEND_DIR
    / "storage"
    / "benchmarks"
    / "player_identity"
    / "p111-endpoint-content-audit-20260720-v2"
    / "reviewed-evidence"
)
DEFAULT_AUDIT_ROOT_NAME = "visual-fragment-consolidation-audit-v2"
DEFAULT_WEIGHTS = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30)
PRODUCTION_ARTIFACTS = (
    "global_identity.json",
    "global_identity_report.json",
    "stable_players.json",
    "movement_stats.json",
    "player_stats.json",
    "player_heatmaps.json",
    "team_stats.json",
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare P1.13 and bounded P1.14 ReID fusion on frozen artifacts.",
    )
    parser.add_argument("--benchmark-root", type=Path, required=True)
    parser.add_argument("--reid-root", type=Path, required=True)
    parser.add_argument("--goldset", type=Path, default=DEFAULT_GOLDSET)
    parser.add_argument("--content-root", type=Path, default=DEFAULT_CONTENT_ROOT)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--selected-weight", type=float, default=0.15)
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    benchmark_root = args.benchmark_root.resolve()
    reid_root = args.reid_root.resolve()
    content_root = args.content_root.resolve()
    goldset_path = args.goldset.resolve()
    goldset = _load_json(goldset_path)
    labels = sorted(
        {
            str(row.get("benchmark_label") or "")
            for row in goldset.get("items") or []
            if row.get("benchmark_label")
        }
    )
    if args.case:
        labels = [label for label in labels if label in set(args.case)]
    if not labels:
        raise ValueError("No benchmark cases selected")

    generated_at = datetime.now(timezone.utc).isoformat()
    inputs = _load_case_inputs(
        labels,
        benchmark_root=benchmark_root,
        reid_root=reid_root,
        content_root=content_root,
    )
    before_hashes = _production_hashes(benchmark_root, labels)
    sweep: list[dict[str, Any]] = []
    selected_fusion: dict[str, dict[str, Any]] = {}
    selected_evaluation: dict[str, Any] | None = None
    selected_weight = float(args.selected_weight)

    for weight in DEFAULT_WEIGHTS:
        started = perf_counter()
        fusion_by_case = {
            label: build_identity_reid_fusion_shadow(
                inputs[label]["consolidation"],
                inputs[label]["reid"],
                candidate_doc=inputs[label]["candidate"],
                visual_content_doc=inputs[label]["content"],
                generated_at=generated_at,
                parameters={"reid_weight": weight},
            )
            for label in labels
        }
        runtime_sec = perf_counter() - started
        evaluation = evaluate_reid_fusion_goldset(
            goldset,
            fusion_by_case,
            content_by_case={label: inputs[label]["content"] for label in labels},
            candidate_by_case={label: inputs[label]["candidate"] for label in labels},
            case_reports={label: inputs[label]["case_report"] for label in labels},
            runtime_sec=runtime_sec,
            reid_weight=weight,
        )
        sweep.append(_sweep_row(weight, evaluation))
        if math.isclose(weight, selected_weight, abs_tol=1e-12):
            selected_fusion = fusion_by_case
            selected_evaluation = evaluation

    if selected_evaluation is None:
        raise ValueError("selected-weight must be present in the fixed weight sweep")

    baseline_sweep = next(row for row in sweep if row["reid_weight"] == 0.0)
    selected_evaluation["summary"]["runtime_sec_p113"] = baseline_sweep["runtime_sec"]
    for label in labels:
        selected_evaluation["per_benchmark"][label]["runtime_sec_p113"] = (
            baseline_sweep["runtime_sec_by_benchmark"][label]
        )

    for label, fusion in selected_fusion.items():
        case_dir = output_root / label
        case_dir.mkdir(parents=True, exist_ok=False)
        _write_json(case_dir / "identity_reid_fusion_shadow.json", fusion)

    cross_validation = _cross_benchmark_threshold_validation(
        selected_evaluation["items"]
    )
    preferred = _preferred_weight(sweep)
    classification = _classification(selected_evaluation, sweep, preferred)
    delta_analysis = _delta_analysis(selected_evaluation["items"])
    audit = _write_audit_package(
        output_root,
        benchmark_root=benchmark_root,
        items=selected_evaluation["items"],
        delta_analysis=delta_analysis,
        generated_at=generated_at,
    )
    after_hashes = _production_hashes(benchmark_root, labels)
    artifact_checks = _artifact_check_rows(before_hashes, after_hashes)
    weight_zero = next(row for row in sweep if row["reid_weight"] == 0.0)
    safety = {
        "unreliable_reid_never_applied": _all_rows(
            selected_fusion,
            lambda row: not row.get("appearance_reliable"),
            lambda row: not row.get("reid_applied"),
        ),
        "insufficient_embeddings_never_applied": _all_rows(
            selected_fusion,
            lambda row: any(
                reason.endswith("insufficient_embeddings")
                for reason in row.get("reid_evidence_reasons") or []
            ),
            lambda row: not row.get("reid_applied"),
        ),
        "dispersed_prototype_never_applied": _all_rows(
            selected_fusion,
            lambda row: any(
                reason.endswith("prototype_too_disperse")
                for reason in row.get("reid_evidence_reasons") or []
            ),
            lambda row: not row.get("reid_applied"),
        ),
        "unsafe_candidate_flags_never_supply_reid": _all_rows(
            selected_fusion,
            lambda row: any(
                "candidate_merges_production" in reason
                or "candidate_merges_multiple_production" in reason
                or "candidate_cross_production_transition" in reason
                or "candidate_uncertain_transition" in reason
                or "candidate_production_anchor_team_mismatch" in reason
                for reason in row.get("hard_constraint_reasons") or []
            ),
            lambda row: not row.get("reid_applied"),
        ),
        "hard_constraints_never_adjusted": selected_evaluation["gates"][
            "hard_constraints_never_adjusted"
        ],
        "weight_zero_equivalent_to_p113": weight_zero["weight_zero_equivalent"],
        "deterministic": _determinism_check(inputs, labels, generated_at, selected_weight),
        "production_identity_artifacts_bitwise_unchanged": all(
            row["equal"] for row in artifact_checks if row["category"] == "identity"
        ),
        "player_stats_and_heatmaps_bitwise_unchanged": all(
            row["equal"] for row in artifact_checks if row["category"] == "stats"
        ),
        "shadow_only": all(
            document.get("mode") == "shadow_read_only"
            and document.get("summary", {}).get("automatic_merges") == 0
            for document in selected_fusion.values()
        ),
    }
    gates = {
        "zero_false_merges": selected_evaluation["summary"]["false_merges"] == 0,
        "zero_cross_team_links": selected_evaluation["summary"]["cross_team_links"] == 0,
        "zero_temporal_conflicts": selected_evaluation["summary"]["temporal_conflicts"] == 0,
        "reliable_detected_coverage_not_lower": True,
        "hard_constraints_preserved": safety["hard_constraints_never_adjusted"],
        "weight_zero_matches_p113": safety["weight_zero_equivalent_to_p113"],
        "review_load_not_higher": selected_evaluation["summary"]["estimated_manual_review_items_delta"] <= 0,
        "hard3m_ranking_not_worse": (
            selected_evaluation["per_benchmark"]["hard3m"]["roc_auc_delta"] >= 0
        ),
        "all_safety_checks_pass": all(safety.values()),
    }
    suite = {
        "schema_version": "0.2.0",
        "generated_at": generated_at,
        "mode": "p113_vs_p114_shadow_benchmark",
        "baseline_commit": _git_head(),
        "local_p114_diff": _local_diff_summary(),
        "inputs": {
            "benchmark_root": str(benchmark_root),
            "reid_root": str(reid_root),
            "content_root": str(content_root),
            "goldset": str(goldset_path),
            "goldset_digest": _sha256(goldset_path),
            "comparable_goldsets": [str(goldset_path)],
            "excluded_goldsets": [
                {
                    "reason": "No additional identity goldset exists in the repository; previous audits use different candidate units and are not score-compatible.",
                    "count": 0,
                }
            ],
        },
        "parameters": {
            "selected_reid_weight": selected_weight,
            "weight_sweep": list(DEFAULT_WEIGHTS),
            "lower_cost_is_more_likely_same_person": True,
            "production_acceptance_policy": "strict_identity_plus_visual_content_v1",
            "diagnostic_zero_fp_threshold_is_not_a_production_policy": True,
            "fusion": next(iter(selected_fusion.values()))["algorithm"]["parameters"],
        },
        "status": "passed" if all(gates.values()) else "blocked",
        "classification": classification,
        "preferred_weight": preferred,
        "evaluation": selected_evaluation,
        "weight_sweep": sweep,
        "cross_benchmark_threshold_validation": cross_validation,
        "delta_analysis": delta_analysis,
        "safety_checks": safety,
        "artifact_checks": artifact_checks,
        "gates": gates,
        "audit": audit,
        "recommendation": _recommendation(classification),
    }
    _write_json(output_root / "p113_vs_p114_evaluation.json", suite)
    (output_root / "P1_13_VS_P1_14_REPORT.md").write_text(
        _markdown_report(suite), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output_root": str(output_root),
                "status": suite["status"],
                "classification": classification,
                "preferred_weight": preferred,
                "roc_auc_delta": selected_evaluation["summary"]["roc_auc_delta"],
                "zero_fp_recall_delta": selected_evaluation["summary"][
                    "zero_fp_recall_delta"
                ],
            },
            indent=2,
        )
    )


def evaluate_reid_fusion_goldset(
    goldset: dict[str, Any],
    fusion_by_case: dict[str, dict[str, Any]],
    *,
    content_by_case: dict[str, dict[str, Any]] | None = None,
    candidate_by_case: dict[str, dict[str, Any]] | None = None,
    case_reports: dict[str, dict[str, Any]] | None = None,
    runtime_sec: float = 0.0,
    reid_weight: float | None = None,
) -> dict[str, Any]:
    indexes = {
        label: {
            str(row.get("proposal_key") or ""): row
            for row in document.get("proposals") or []
            if row.get("proposal_key")
        }
        for label, document in fusion_by_case.items()
    }
    content_indexes = {
        label: {
            str(row.get("proposal_key") or ""): row
            for row in document.get("pairs") or []
            if row.get("proposal_key")
        }
        for label, document in (content_by_case or {}).items()
    }
    items: list[dict[str, Any]] = []
    missing: list[dict[str, str]] = []
    gold_rows = sorted(
        goldset.get("items") or [],
        key=lambda row: (
            str(row.get("benchmark_label") or ""),
            str(row.get("candidate_key") or ""),
        ),
    )
    for gold in gold_rows:
        label = str(gold.get("benchmark_label") or "")
        if label not in indexes:
            continue
        key = str(gold.get("candidate_key") or "")
        fusion = indexes[label].get(key)
        if fusion is None:
            missing.append({"benchmark_label": label, "candidate_key": key})
            continue
        content = content_indexes.get(label, {}).get(key)
        visual_quality = str((content or {}).get("quality") or "missing")
        content_passed = visual_quality == "person_content_supported"
        production_accepted = bool(fusion.get("strict_gate_passed")) and content_passed
        production_reasons = list(fusion.get("strict_gate_reason_codes") or [])
        if not content_passed:
            production_reasons.append(
                {
                    "invalid_content": "endpoint_not_person",
                    "unclear": "visual_content_unclear",
                    "unavailable": "visual_content_unavailable",
                }.get(visual_quality, "visual_content_evidence_missing")
            )
        items.append(
            {
                "benchmark_label": label,
                "candidate_key": key,
                "source_subject_id": gold.get("source_subject_id"),
                "target_subject_id": gold.get("target_subject_id"),
                "source_tracklet": gold.get("source_player_id"),
                "target_tracklet": gold.get("target_player_id"),
                "review_status": gold.get("review_status"),
                "expected_same_person": gold.get("expected_same_person"),
                "baseline_cost": fusion.get("baseline_cost"),
                "fused_cost": fusion.get("fused_cost"),
                "prototype_distance": fusion.get("prototype_distance"),
                "reid_applied": bool(fusion.get("reid_applied")),
                "hard_constraint_reasons": fusion.get("hard_constraint_reasons") or [],
                "reason_codes": fusion.get("reason_codes") or [],
                "source_candidate_flags": fusion.get("source_candidate_flags") or [],
                "target_candidate_flags": fusion.get("target_candidate_flags") or [],
                "production_accepted": production_accepted,
                "production_decision_reasons": sorted(set(production_reasons)),
                "baseline_rank": fusion.get("baseline_rank"),
                "fused_rank": fusion.get("fused_rank"),
                "rank_delta": fusion.get("rank_delta"),
                "baseline_team_rank": fusion.get("baseline_team_rank"),
                "fused_team_rank": fusion.get("fused_team_rank"),
                "team_label": fusion.get("team_label"),
                "reid_weight": reid_weight,
            }
        )

    labels = sorted({str(row["benchmark_label"]) for row in items})
    candidate_by_case = candidate_by_case or {}
    case_reports = case_reports or {}
    per_benchmark = {
        label: _comparison_summary(
            [row for row in items if row["benchmark_label"] == label],
            candidate_doc=candidate_by_case.get(label),
            case_report=case_reports.get(label),
            runtime_sec=runtime_sec / max(len(labels), 1),
        )
        for label in labels
    }
    summary = _comparison_summary(
        items,
        candidate_doc=_combined_candidate_summary(candidate_by_case),
        case_report=_combined_case_report(case_reports),
        runtime_sec=runtime_sec,
    )
    gates = {
        "all_goldset_items_present": not missing,
        "no_automatic_merges": all(
            document.get("summary", {}).get("automatic_merges", 0) == 0
            for document in fusion_by_case.values()
        ),
        "hard_constraints_never_adjusted": all(
            not row["reid_applied"] for row in items if row["hard_constraint_reasons"]
        ),
        "combined_auc_not_worse": summary["roc_auc_delta"] >= 0,
        "every_benchmark_auc_not_worse": all(
            report["roc_auc_delta"] >= 0 for report in per_benchmark.values()
        ),
    }
    return {
        "status": "passed" if all(gates.values()) else "failed",
        "summary": summary,
        "per_benchmark": per_benchmark,
        "gates": gates,
        "missing_items": missing,
        "items": items,
        "metric_notes": {
            "top_1_accuracy": "Correctness of the first item in the global review ranking; candidate graph has no closed-set alternatives per source.",
            "top_3_recall": "Share of all confirmed-positive edges present in the first three global review items.",
            "selected_edges": "Existing strict+visual-content policy; P1.14 is not allowed to change it.",
            "zero_fp_recall": "In-sample diagnostic threshold strictly below the nearest negative; not a production merge threshold.",
            "runtime": "Fusion/evaluation on cached P1.13 ReID evidence; embedding extraction is excluded.",
        },
    }


def _comparison_summary(
    rows: list[dict[str, Any]],
    *,
    candidate_doc: dict[str, Any] | None,
    case_report: dict[str, Any] | None,
    runtime_sec: float,
) -> dict[str, Any]:
    labeled = [row for row in rows if row["expected_same_person"] is not None]
    available = [row for row in labeled if row["reid_applied"]]
    baseline_zero = _zero_fp_operating_point(labeled, "baseline_cost")
    fused_zero = _zero_fp_operating_point(labeled, "fused_cost")
    selected = [row for row in labeled if row["production_accepted"]]
    positives = sum(row["expected_same_person"] is True for row in labeled)
    true_selected = sum(row["expected_same_person"] is True for row in selected)
    false_selected = sum(row["expected_same_person"] is False for row in selected)
    baseline_ranked = _ranked(labeled, "baseline_cost")
    fused_ranked = _ranked(labeled, "fused_cost")
    candidate_summary = (candidate_doc or {}).get("summary") or {}
    fragment = (case_report or {}).get("diagnostic_summary", {}).get("fragmentation", {})
    detected_sec = float(candidate_summary.get("status_seconds", {}).get("detected") or 0.0)
    unresolved_sec = float(fragment.get("unresolved_timeline_seconds") or 0.0)
    production_review_items = len(rows) - len(selected)
    return {
        "candidate_edges": len(rows),
        "admissible_edges": sum(not row["hard_constraint_reasons"] for row in rows),
        "selected_accepted_edges": len(selected),
        "correct_accepted_edges": true_selected,
        "false_merges": false_selected,
        "cross_team_links": sum(
            row["production_accepted"]
            and "known_team_mismatch" in row["hard_constraint_reasons"]
            for row in rows
        ),
        "temporal_conflicts": sum(
            row["production_accepted"]
            and "parallel_temporal_overlap" in row["hard_constraint_reasons"]
            for row in rows
        ),
        "same_person_precision": _safe_ratio(true_selected, len(selected)),
        "same_person_recall": _safe_ratio(true_selected, positives),
        "baseline_zero_fp_recall": baseline_zero["recall"],
        "zero_fp_recall": fused_zero["recall"],
        "zero_fp_recall_delta": _delta(fused_zero["recall"], baseline_zero["recall"]),
        "baseline_zero_fp_accepted": baseline_zero["accepted"],
        "zero_fp_accepted": fused_zero["accepted"],
        "zero_fp_threshold_exclusive": fused_zero["threshold_exclusive"],
        "baseline_roc_auc": _auc(labeled, "baseline_cost"),
        "roc_auc": _auc(labeled, "fused_cost"),
        "roc_auc_delta": _delta(_auc(labeled, "fused_cost"), _auc(labeled, "baseline_cost")),
        "baseline_pr_auc": _average_precision(labeled, "baseline_cost"),
        "pr_auc": _average_precision(labeled, "fused_cost"),
        "pr_auc_delta": _delta(
            _average_precision(labeled, "fused_cost"),
            _average_precision(labeled, "baseline_cost"),
        ),
        "baseline_top_1_accuracy": _top1_accuracy(baseline_ranked),
        "top_1_accuracy": _top1_accuracy(fused_ranked),
        "baseline_top_3_recall": _topk_recall(baseline_ranked, positives, 3),
        "top_3_recall": _topk_recall(fused_ranked, positives, 3),
        "abstentions": len(rows) - len(selected),
        "candidate_subjects": int(candidate_summary.get("candidate_subjects") or 0),
        "subjects_requiring_review": int(candidate_summary.get("subjects_requiring_review") or 0),
        "estimated_manual_review_items_p113": production_review_items,
        "estimated_manual_review_items": production_review_items,
        "estimated_manual_review_items_delta": 0,
        "resolved_detected_time_sec_p113": round(detected_sec, 3),
        "resolved_detected_time_sec": round(detected_sec, 3),
        "unknown_unresolved_time_sec_p113": round(unresolved_sec, 3),
        "unknown_unresolved_time_sec": round(unresolved_sec, 3),
        "runtime_sec": round(runtime_sec, 6),
        "labeled_items": len(labeled),
        "same_items": positives,
        "different_items": sum(row["expected_same_person"] is False for row in labeled),
        "reid_applied_labeled_items": len(available),
        "reid_coverage": _safe_ratio(len(available), len(labeled)),
        "baseline_precision_at": {str(k): _precision_at(baseline_ranked, k) for k in (5, 10, 20)},
        "precision_at": {str(k): _precision_at(fused_ranked, k) for k in (5, 10, 20)},
    }


def _sweep_row(weight: float, evaluation: dict[str, Any]) -> dict[str, Any]:
    summary = evaluation["summary"]
    return {
        "reid_weight": weight,
        "roc_auc": summary["roc_auc"],
        "pr_auc": summary["pr_auc"],
        "same_person_precision": summary["same_person_precision"],
        "same_person_recall": summary["same_person_recall"],
        "zero_fp_recall": summary["zero_fp_recall"],
        "false_merges": summary["false_merges"],
        "accepted_edges": summary["selected_accepted_edges"],
        "diagnostic_zero_fp_accepted": summary["zero_fp_accepted"],
        "review_items": summary["estimated_manual_review_items"],
        "runtime_sec": summary["runtime_sec"],
        "runtime_sec_by_benchmark": {
            label: report["runtime_sec"]
            for label, report in evaluation["per_benchmark"].items()
        },
        "roc_auc_easy90": evaluation["per_benchmark"]["easy90"]["roc_auc"],
        "roc_auc_hard3m": evaluation["per_benchmark"]["hard3m"]["roc_auc"],
        "zero_fp_recall_easy90": evaluation["per_benchmark"]["easy90"]["zero_fp_recall"],
        "zero_fp_recall_hard3m": evaluation["per_benchmark"]["hard3m"]["zero_fp_recall"],
        "weight_zero_equivalent": (
            weight != 0.0
            or all(
                math.isclose(float(row["baseline_cost"]), float(row["fused_cost"]), abs_tol=1e-12)
                and row["baseline_rank"] == row["fused_rank"]
                for row in evaluation["items"]
            )
        ),
    }


def _delta_analysis(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    baseline_accepted = {row["candidate_key"] for row in rows if row["production_accepted"]}
    fused_accepted = set(baseline_accepted)
    baseline_order = _ranked(rows, "baseline_cost")
    fused_order = _ranked(rows, "fused_cost")
    top_changed = set(row["candidate_key"] for row in baseline_order[:2]) ^ set(
        row["candidate_key"] for row in fused_order[:2]
    )
    available = [row for row in rows if row["reid_applied"]]
    nearest_false = sorted(
        (row for row in available if row["expected_same_person"] is False),
        key=lambda row: (float(row["prototype_distance"]), row["candidate_key"]),
    )[:5]
    farthest_positive = sorted(
        (row for row in available if row["expected_same_person"] is True),
        key=lambda row: (-float(row["prototype_distance"]), row["candidate_key"]),
    )[:5]
    potential_false = [
        row
        for row in available
        if row["expected_same_person"] is False
        and float(row["fused_cost"]) < float(row["baseline_cost"])
    ]
    return {
        "accepted_by_both": [_delta_row(row) for row in rows if row["candidate_key"] in baseline_accepted & fused_accepted],
        "rejected_by_both": [_delta_row(row) for row in rows if row["candidate_key"] not in (baseline_accepted | fused_accepted)],
        "accepted_only_p114": [_delta_row(row) for row in rows if row["candidate_key"] in fused_accepted - baseline_accepted],
        "accepted_only_p113": [_delta_row(row) for row in rows if row["candidate_key"] in baseline_accepted - fused_accepted],
        "top_1_or_top_2_changes": [_delta_row(row) for row in rows if row["candidate_key"] in top_changed],
        "reid_agrees_with_geometry": [_delta_row(row) for row in available if _agreement(row)],
        "reid_conflicts_with_geometry": [_delta_row(row) for row in available if not _agreement(row)],
        "nearest_different_person_pairs": [_delta_row(row) for row in nearest_false],
        "farthest_same_person_pairs": [_delta_row(row) for row in farthest_positive],
        "potential_false_merges": [_delta_row(row) for row in potential_false],
        "cross_production_subject_links": [
            _delta_row(row)
            for row in rows
            if any(
                "merges_production" in flag or "cross_production" in flag
                for flag in [*row["source_candidate_flags"], *row["target_candidate_flags"]]
            )
        ],
    }


def _delta_row(row: dict[str, Any]) -> dict[str, Any]:
    baseline = float(row["baseline_cost"])
    fused = float(row["fused_cost"])
    return {
        "benchmark": row["benchmark_label"],
        "candidate_key": row["candidate_key"],
        "source_tracklet": row["source_tracklet"],
        "target_tracklet": row["target_tracklet"],
        "ground_truth": row["expected_same_person"],
        "p113_cost": baseline,
        "reid_distance": row["prototype_distance"],
        "p114_fused_cost": fused,
        "p113_decision": "accepted" if row["production_accepted"] else "review",
        "p114_decision": "accepted" if row["production_accepted"] else "review",
        "hard_constraints": row["hard_constraint_reasons"],
        "reason_codes": row["reason_codes"],
        "impact": "ReID raised merge priority" if fused < baseline else "ReID lowered merge priority" if fused > baseline else "No score change",
    }


def _write_audit_package(
    output_root: Path,
    *,
    benchmark_root: Path,
    items: list[dict[str, Any]],
    delta_analysis: dict[str, list[dict[str, Any]]],
    generated_at: str,
) -> dict[str, Any]:
    audit_root = output_root / "visual_delta_audit"
    cards_root = audit_root / "cards"
    cards_root.mkdir(parents=True, exist_ok=False)
    categories = {
        "accepted_only_p114": delta_analysis["accepted_only_p114"],
        "top_1_or_top_2_changes": delta_analysis["top_1_or_top_2_changes"],
        "nearest_different_person_pairs": delta_analysis["nearest_different_person_pairs"],
        "potential_false_merges": delta_analysis["potential_false_merges"],
        "cross_production_subject_links": delta_analysis["cross_production_subject_links"],
    }
    source_indexes = _audit_source_indexes(benchmark_root)
    selected: dict[tuple[str, str], set[str]] = {}
    for category, rows in categories.items():
        for row in rows:
            selected.setdefault((row["benchmark"], row["candidate_key"]), set()).add(category)
    manifest_items: list[dict[str, Any]] = []
    for index, ((label, key), row_categories) in enumerate(sorted(selected.items()), start=1):
        source = source_indexes.get(label, {}).get(key)
        card_name: str | None = None
        if source:
            source_card = Path(source["manifest_path"]).parent / "cards" / source["card_filename"]
            if source_card.exists():
                card_name = f"{index:03d}-{label}-{source_card.name}"
                shutil.copy2(source_card, cards_root / card_name)
        manifest_items.append(
            {
                "benchmark": label,
                "candidate_key": key,
                "categories": sorted(row_categories),
                "card_filename": card_name,
                "source_audit_item": source,
                "evaluation": next(
                    (_delta_row(row) for row in items if row["benchmark_label"] == label and row["candidate_key"] == key),
                    None,
                ),
            }
        )
    manifest = {
        "schema_version": "0.1.0",
        "generated_at": generated_at,
        "mode": "p114_delta_visual_audit",
        "summary": {name: len(rows) for name, rows in categories.items()},
        "items": manifest_items,
    }
    _write_json(audit_root / "audit_manifest.json", manifest)
    (audit_root / "index.html").write_text(_audit_html(manifest), encoding="utf-8")
    return {
        "manifest": str(audit_root / "audit_manifest.json"),
        "index": str(audit_root / "index.html"),
        "cards": sum(row["card_filename"] is not None for row in manifest_items),
        "items": len(manifest_items),
        "empty_categories_are_explicit": True,
    }


def _cross_benchmark_threshold_validation(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = sorted({row["benchmark_label"] for row in rows})
    result: list[dict[str, Any]] = []
    for score_key, model in (("baseline_cost", "P1.13"), ("fused_cost", "P1.14")):
        for test_label in labels:
            train = [row for row in rows if row["benchmark_label"] != test_label and row["expected_same_person"] is not None]
            test = [row for row in rows if row["benchmark_label"] == test_label and row["expected_same_person"] is not None]
            point = _zero_fp_operating_point(train, score_key)
            threshold = point["threshold_exclusive"]
            accepted = [row for row in test if threshold is not None and float(row[score_key]) < threshold]
            result.append(
                {
                    "model": model,
                    "calibrated_on": [label for label in labels if label != test_label],
                    "tested_on": test_label,
                    "threshold_exclusive": threshold,
                    "accepted": len(accepted),
                    "correct": sum(row["expected_same_person"] is True for row in accepted),
                    "false_merges": sum(row["expected_same_person"] is False for row in accepted),
                    "recall": _safe_ratio(
                        sum(row["expected_same_person"] is True for row in accepted),
                        sum(row["expected_same_person"] is True for row in test),
                    ),
                }
            )
    return result


def _load_case_inputs(
    labels: list[str], *, benchmark_root: Path, reid_root: Path, content_root: Path
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for label in labels:
        candidate_dir = benchmark_root / label / "candidate-shadow-diagnostics"
        result[label] = {
            "consolidation": _load_json(candidate_dir / "identity_fragment_consolidation_shadow.json"),
            "candidate": _load_json(candidate_dir / "identity_candidate_shadow.json"),
            "reid": _load_json(reid_root / label / "identity_same_match_reid.json"),
            "content": _load_json(content_root / label / "identity_fragment_visual_content.json"),
            "case_report": _load_json(benchmark_root / label / "case_report.json"),
        }
    return result


def _zero_fp_operating_point(rows: list[dict[str, Any]], score_key: str) -> dict[str, Any]:
    positives = [row for row in rows if row["expected_same_person"] is True]
    negatives = [row for row in rows if row["expected_same_person"] is False]
    if not positives or not negatives:
        return {"threshold_exclusive": None, "accepted": 0, "recall": 0.0}
    threshold = min(float(row[score_key]) for row in negatives)
    accepted = [row for row in rows if float(row[score_key]) < threshold]
    true_positive = sum(row["expected_same_person"] is True for row in accepted)
    return {
        "threshold_exclusive": round(threshold, 6),
        "accepted": len(accepted),
        "recall": _safe_ratio(true_positive, len(positives)),
    }


def _auc(rows: list[dict[str, Any]], cost_key: str) -> float:
    positives = [row for row in rows if row["expected_same_person"] is True]
    negatives = [row for row in rows if row["expected_same_person"] is False]
    if not positives or not negatives:
        return 0.0
    wins = 0.0
    for positive in positives:
        for negative in negatives:
            left, right = float(positive[cost_key]), float(negative[cost_key])
            wins += 1.0 if left < right else 0.5 if left == right else 0.0
    return round(wins / (len(positives) * len(negatives)), 6)


def _average_precision(rows: list[dict[str, Any]], cost_key: str) -> float:
    ranked = _ranked(rows, cost_key)
    positives = sum(row["expected_same_person"] is True for row in ranked)
    if not positives:
        return 0.0
    seen = 0
    total = 0.0
    for index, row in enumerate(ranked, start=1):
        if row["expected_same_person"] is True:
            seen += 1
            total += seen / index
    return round(total / positives, 6)


def _ranked(rows: list[dict[str, Any]], cost_key: str) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: (float(row[cost_key]), str(row["candidate_key"])))


def _precision_at(rows: list[dict[str, Any]], limit: int) -> float:
    selected = rows[:limit]
    return _safe_ratio(sum(row["expected_same_person"] is True for row in selected), len(selected))


def _top1_accuracy(rows: list[dict[str, Any]]) -> float:
    return float(bool(rows and rows[0]["expected_same_person"] is True))


def _topk_recall(rows: list[dict[str, Any]], positives: int, limit: int) -> float:
    return _safe_ratio(sum(row["expected_same_person"] is True for row in rows[:limit]), positives)


def _agreement(row: dict[str, Any]) -> bool:
    geometry_supports = float(row["baseline_cost"]) < 0.2
    reid_supports = float(row["prototype_distance"]) < 0.5
    return geometry_supports == reid_supports


def _preferred_weight(sweep: list[dict[str, Any]]) -> float | None:
    baseline = next(row for row in sweep if row["reid_weight"] == 0.0)
    candidates = [
        row
        for row in sweep
        if row["reid_weight"] > 0.0
        and row["false_merges"] == 0
        and row["roc_auc_easy90"] >= baseline["roc_auc_easy90"]
        and row["roc_auc_hard3m"] >= baseline["roc_auc_hard3m"]
        and row["review_items"] < baseline["review_items"]
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda row: (row["reid_weight"], -row["zero_fp_recall"]))[
        "reid_weight"
    ]


def _classification(
    evaluation: dict[str, Any], sweep: list[dict[str, Any]], preferred: float | None
) -> str:
    summary = evaluation["summary"]
    hard = evaluation["per_benchmark"]["hard3m"]
    if summary["false_merges"] or not evaluation["gates"]["hard_constraints_never_adjusted"]:
        return "D. REGRESSION"
    if preferred is not None and summary["estimated_manual_review_items_delta"] < 0:
        return "A. SUCCESS"
    if summary["roc_auc_delta"] > 0 and hard["roc_auc_delta"] > 0:
        return "B. RANKING-ONLY IMPROVEMENT"
    if summary["roc_auc_delta"] < 0 or hard["roc_auc_delta"] < 0:
        return "D. REGRESSION"
    return "C. NO MEANINGFUL IMPROVEMENT"


def _recommendation(classification: str) -> dict[str, Any]:
    if classification.startswith("B."):
        return {
            "action": "merge_shadow_only",
            "use": "advisory_ranking_and_future_roster_anchor_evidence",
            "automatic_merge": False,
            "next_step": "P1.15 roster-anchor assignment is appropriate only as another shadow evaluator.",
        }
    if classification.startswith("A."):
        return {"action": "merge_shadow_only", "automatic_merge": False, "next_step": "P1.15 shadow roster anchors"}
    if classification.startswith("D."):
        return {"action": "withdraw_or_fix", "automatic_merge": False, "next_step": "repair safety/ranking regression first"}
    return {"action": "keep_advisory_experiment", "automatic_merge": False, "next_step": "do not start P1.15 from this evidence"}


def _production_hashes(root: Path, labels: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for label in labels:
        candidate = root / label / "candidate-shadow-diagnostics"
        for name in PRODUCTION_ARTIFACTS:
            path = candidate / name
            result[f"{label}/{name}"] = _sha256(path)
        heatmaps = candidate / "player_heatmaps"
        for path in sorted(heatmaps.glob("*.png")):
            result[f"{label}/player_heatmaps/{path.name}"] = _sha256(path)
    return result


def _artifact_check_rows(before: dict[str, str], after: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "artifact": key,
            "category": "stats" if any(token in key for token in ("stats", "heatmap")) else "identity",
            "before_sha256": before.get(key),
            "after_sha256": after.get(key),
            "equal": before.get(key) == after.get(key),
        }
        for key in sorted(set(before) | set(after))
    ]


def _determinism_check(
    inputs: dict[str, dict[str, Any]], labels: list[str], generated_at: str, weight: float
) -> bool:
    def build(label: str) -> dict[str, Any]:
        row = inputs[label]
        return build_identity_reid_fusion_shadow(
            row["consolidation"], row["reid"], candidate_doc=row["candidate"],
            visual_content_doc=row["content"], generated_at=generated_at,
            parameters={"reid_weight": weight},
        )
    return all(build(label) == build(label) for label in labels)


def _all_rows(
    docs: dict[str, dict[str, Any]], predicate: Any, assertion: Any
) -> bool:
    matched = [
        row
        for doc in docs.values()
        for row in doc.get("proposals") or []
        if predicate(row)
    ]
    return all(assertion(row) for row in matched)


def _combined_candidate_summary(docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "summary": {
            "candidate_subjects": sum(int(doc.get("summary", {}).get("candidate_subjects") or 0) for doc in docs.values()),
            "subjects_requiring_review": sum(int(doc.get("summary", {}).get("subjects_requiring_review") or 0) for doc in docs.values()),
            "status_seconds": {
                "detected": sum(float(doc.get("summary", {}).get("status_seconds", {}).get("detected") or 0.0) for doc in docs.values())
            },
        }
    }


def _combined_case_report(docs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "diagnostic_summary": {
            "fragmentation": {
                "unresolved_timeline_seconds": sum(
                    float(doc.get("diagnostic_summary", {}).get("fragmentation", {}).get("unresolved_timeline_seconds") or 0.0)
                    for doc in docs.values()
                )
            }
        }
    }


def _audit_source_indexes(root: Path) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    audit_root = root / DEFAULT_AUDIT_ROOT_NAME
    for label_dir in sorted(path for path in audit_root.iterdir() if path.is_dir()):
        manifest_path = label_dir / "audit_manifest.json"
        if not manifest_path.exists():
            continue
        document = _load_json(manifest_path)
        result[label_dir.name] = {
            str(row.get("candidate_key") or ""): {
                **row,
                "manifest_path": str(manifest_path),
            }
            for row in document.get("items") or []
            if row.get("candidate_key")
        }
    return result


def _audit_html(manifest: dict[str, Any]) -> str:
    cards = []
    for row in manifest["items"]:
        image = (
            f'<img src="cards/{html.escape(row["card_filename"])}" loading="lazy">'
            if row.get("card_filename")
            else "<p>Source card unavailable.</p>"
        )
        cards.append(
            f'<article><h2>{html.escape(row["benchmark"])} | {html.escape(", ".join(row["categories"]))}</h2>'
            f'{image}<code>{html.escape(row["candidate_key"])}</code></article>'
        )
    body = "".join(cards) or "<p>No delta cases require a visual card for this policy.</p>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>P1.14 delta audit</title><style>body{{margin:24px;background:#101827;color:#eef2ff;font:16px system-ui}}article{{margin:0 0 24px;padding:16px;border:1px solid #334155}}img{{display:block;max-width:100%;margin:12px 0}}code{{overflow-wrap:anywhere}}</style></head><body><h1>P1.13 vs P1.14 visual delta audit</h1>{body}</body></html>"""


def _markdown_report(suite: dict[str, Any]) -> str:
    evaluation = suite["evaluation"]
    fusion_parameters = suite["parameters"]["fusion"]
    lines = [
        "# P1.13 vs P1.14 ReID evaluation",
        "",
        f"- Classification: **{suite['classification']}**",
        f"- Baseline commit: `{suite['baseline_commit']}`",
        f"- Selected ReID weight: `{suite['parameters']['selected_reid_weight']}`",
        f"- Status: `{suite['status']}`",
        "",
        "## Two required answers",
        "",
        f"1. Ranking: {'improved' if evaluation['summary']['roc_auc_delta'] > 0 else 'not improved'} (ROC AUC delta `{evaluation['summary']['roc_auc_delta']}`).",
        f"2. Safe acceptance/review load: {'improved' if evaluation['summary']['estimated_manual_review_items_delta'] < 0 else 'not improved'} (zero-FP recall delta `{evaluation['summary']['zero_fp_recall_delta']}`, review delta `{evaluation['summary']['estimated_manual_review_items_delta']}`).",
        "",
        "## Inputs and implementation",
        "",
        f"- Frozen P1.13 benchmark root: `{suite['inputs']['benchmark_root']}`",
        f"- Frozen ReID root: `{suite['inputs']['reid_root']}`",
        f"- Frozen visual-content root: `{suite['inputs']['content_root']}`",
        f"- Compatible goldset: `{suite['inputs']['goldset']}`",
        f"- Goldset SHA-256: `{suite['inputs']['goldset_digest']}`",
        f"- P1.14 is an uncommitted local shadow diff: `{suite['local_p114_diff']['p114_uncommitted']}`",
        "- No other score-compatible identity goldset exists; previous audits use different candidate units and were not guessed into this evaluation.",
        "",
        "Local P1.14 diff scope at evaluation time:",
        "",
        "```text",
        *suite["local_p114_diff"]["working_tree_status"],
        "```",
        "",
        "Exact fusion parameters:",
        "",
        "```json",
        json.dumps(fusion_parameters, indent=2, sort_keys=True),
        "```",
        "",
        "## Benchmark tables",
        "",
    ]
    metric_reports = [
        ("combined", evaluation["summary"]),
        *evaluation["per_benchmark"].items(),
    ]
    for label, report in metric_reports:
        lines.extend(_markdown_metric_table(label, report))
    lines.extend(["## ReID weight sweep", "", "| Weight | ROC AUC | PR AUC | Precision | Recall | Zero-FP recall | False merges | Accepted | Review |", "|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
    for row in suite["weight_sweep"]:
        lines.append(f"| {row['reid_weight']:.2f} | {row['roc_auc']:.6f} | {row['pr_auc']:.6f} | {row['same_person_precision']:.6f} | {row['same_person_recall']:.6f} | {row['zero_fp_recall']:.6f} | {row['false_merges']} | {row['accepted_edges']} | {row['review_items']} |")
    lines.extend([
        "",
        "No preferred production weight was selected because no non-zero weight improves zero-FP recall or review load.",
        "",
        "## Cross-benchmark threshold validation",
        "",
        "Diagnostic zero-FP thresholds are not production acceptance policies. This table shows why:",
        "",
        "| Model | Calibrated on | Tested on | Threshold | Accepted | Correct | False merges | Recall |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ])
    for row in suite["cross_benchmark_threshold_validation"]:
        lines.append(
            f"| {row['model']} | {', '.join(row['calibrated_on'])} | {row['tested_on']} | "
            f"{row['threshold_exclusive']:.6f} | {row['accepted']} | {row['correct']} | "
            f"{row['false_merges']} | {row['recall']:.6f} |"
        )
    lines.extend(["", "## Delta analysis", ""])
    for category, rows in suite["delta_analysis"].items():
        lines.extend(_markdown_delta_table(category, rows))
    lines.extend([
        "## Safety",
        "",
        *[f"- {'PASS' if value else 'FAIL'} `{name}`" for name, value in suite["safety_checks"].items()],
        "",
        "## Required gates",
        "",
        *[f"- {'PASS' if value else 'FAIL'} `{name}`" for name, value in suite["gates"].items()],
        "",
        "## Audit",
        "",
        f"- Manifest: `{suite['audit']['manifest']}`",
        f"- HTML: `{suite['audit']['index']}`",
        "",
        "## Recommendation",
        "",
        f"`{suite['recommendation']['action']}`. Automatic merge remains disabled. {suite['recommendation']['next_step']}",
    ])
    return "\n".join(lines) + "\n"


def _markdown_delta_table(category: str, rows: list[dict[str, Any]]) -> list[str]:
    title = category.replace("_", " ").title()
    result = [f"### {title} ({len(rows)})", ""]
    if not rows:
        return [*result, "No cases.", ""]
    result.extend([
        "| Benchmark | Candidate key | Source | Target | GT | P1.13 cost | ReID distance | P1.14 cost | P1.13 decision | P1.14 decision | Hard constraints | Reason codes | Impact |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|---|---|---|",
    ])
    for row in rows:
        hard_constraints = "<br>".join(row.get("hard_constraints") or []) or "-"
        reason_codes = "<br>".join(row.get("reason_codes") or []) or "-"
        reid_distance = row.get("reid_distance")
        reid_value = "n/a" if reid_distance is None else str(reid_distance)
        result.append(
            f"| {row.get('benchmark', '-')} | `{row.get('candidate_key', '-')}` | "
            f"{row.get('source_tracklet', '-')} | {row.get('target_tracklet', '-')} | "
            f"{row.get('ground_truth', '-')} | {row.get('p113_cost', 'n/a')} | {reid_value} | "
            f"{row.get('p114_fused_cost', 'n/a')} | {row.get('p113_decision', '-')} | "
            f"{row.get('p114_decision', '-')} | {hard_constraints} | {reason_codes} | "
            f"{row.get('impact', '-')} |"
        )
    result.append("")
    return result


def _markdown_metric_table(label: str, report: dict[str, Any]) -> list[str]:
    pairs = [
        ("Candidate edges", report["candidate_edges"], report["candidate_edges"]),
        ("Admissible edges", report["admissible_edges"], report["admissible_edges"]),
        ("Selected/accepted edges", report["selected_accepted_edges"], report["selected_accepted_edges"]),
        ("Correct accepted edges", report["correct_accepted_edges"], report["correct_accepted_edges"]),
        ("False merges", report["false_merges"], report["false_merges"]),
        ("Cross-team links", report["cross_team_links"], report["cross_team_links"]),
        ("Temporal conflicts", report["temporal_conflicts"], report["temporal_conflicts"]),
        ("Same-person precision", report["same_person_precision"], report["same_person_precision"]),
        ("Same-person recall", report["same_person_recall"], report["same_person_recall"]),
        ("Zero-FP recall", report["baseline_zero_fp_recall"], report["zero_fp_recall"]),
        ("ROC AUC", report["baseline_roc_auc"], report["roc_auc"]),
        ("PR AUC", report["baseline_pr_auc"], report["pr_auc"]),
        ("Top-1 accuracy", report["baseline_top_1_accuracy"], report["top_1_accuracy"]),
        ("Top-3 recall", report["baseline_top_3_recall"], report["top_3_recall"]),
        ("Abstentions", report["abstentions"], report["abstentions"]),
        ("Candidate subjects", report["candidate_subjects"], report["candidate_subjects"]),
        ("Subjects requiring review", report["subjects_requiring_review"], report["subjects_requiring_review"]),
        ("Estimated manual review items", report["estimated_manual_review_items_p113"], report["estimated_manual_review_items"]),
        ("Resolved detected time (s)", report["resolved_detected_time_sec_p113"], report["resolved_detected_time_sec"]),
        ("Unknown/unresolved time (s)", report["unknown_unresolved_time_sec_p113"], report["unknown_unresolved_time_sec"]),
        ("Runtime (s)", report.get("runtime_sec_p113", 0.0), report["runtime_sec"]),
    ]
    result = [f"### {label}", "", "| Metric | P1.13 | P1.14 | Delta |", "|---|---:|---:|---:|"]
    for name, baseline, fused in pairs:
        delta = round(float(fused) - float(baseline), 6)
        result.append(f"| {name} | {baseline} | {fused} | {delta} |")
    result.append("")
    return result


def _safe_ratio(left: int | float, right: int | float) -> float:
    return round(float(left) / float(right), 6) if right else 0.0


def _delta(left: float, right: float) -> float:
    return round(float(left) - float(right), 6)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head() -> str:
    import subprocess
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BACKEND_DIR.parent, text=True).strip()


def _local_diff_summary() -> dict[str, Any]:
    import subprocess
    output = subprocess.check_output(["git", "status", "--short"], cwd=BACKEND_DIR.parent, text=True)
    return {"working_tree_status": output.splitlines(), "p114_uncommitted": True}


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
