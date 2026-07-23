from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_jersey_number_heldout_validation"
ALGORITHM_VERSION = "0.3.0"
CASE_SCHEMA_VERSION = "0.2.0"
CASE_ALGORITHM_NAME = "identity_jersey_number_heldout_case_contract"
CASE_ALGORITHM_VERSION = "0.3.0"
REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS = (
    "global_identity.json",
    "stable_players.json",
    "player_identity_assignments.json",
)


def build_production_identity_artifact_comparison(
    before: dict[str, str | None],
    after: dict[str, str | None],
    *,
    required_artifacts: tuple[str, ...] = REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS,
) -> dict[str, Any]:
    """Compare immutable production identity snapshots without trusting a boolean."""
    rows = []
    for name in sorted(set(before) | set(after) | set(required_artifacts)):
        before_digest = before.get(name)
        after_digest = after.get(name)
        rows.append(
            {
                "artifact": name,
                "required": name in required_artifacts,
                "before_sha256": before_digest,
                "after_sha256": after_digest,
                "present_before": bool(before_digest),
                "present_after": bool(after_digest),
                "equal": bool(before_digest and after_digest and before_digest == after_digest),
            }
        )
    required_rows = [row for row in rows if row["required"]]
    missing = [
        row["artifact"]
        for row in required_rows
        if not row["present_before"] or not row["present_after"]
    ]
    changed = [
        row["artifact"]
        for row in required_rows
        if row["present_before"] and row["present_after"] and not row["equal"]
    ]
    unchanged = bool(required_rows) and not missing and not changed
    return {
        "production_identity_unchanged": unchanged,
        "required_artifacts": list(required_artifacts),
        "checked_artifacts": len(rows),
        "missing_required_artifacts": missing,
        "changed_required_artifacts": changed,
        "artifacts": rows,
    }


def build_identity_jersey_number_heldout_case_contract(
    *,
    benchmark_id: str,
    source_match_key: str,
    recognizer_doc: dict[str, Any],
    assignment_doc: dict[str, Any],
    propagation_doc: dict[str, Any],
    targeted_evaluation_doc: dict[str, Any],
    production_before: dict[str, str | None],
    production_after: dict[str, str | None],
    held_out: bool = True,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build a compact N5.8 case from evidence and verified production snapshots."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    comparison = build_production_identity_artifact_comparison(
        production_before,
        production_after,
    )
    normalized = _evaluate_case(
        {
            "benchmark_id": benchmark_id,
            "source_match_key": source_match_key,
            "held_out": held_out,
            "production_identity_unchanged": comparison[
                "production_identity_unchanged"
            ],
            "recognizer_doc": recognizer_doc,
            "assignment_doc": assignment_doc,
            "propagation_doc": propagation_doc,
            "targeted_evaluation_doc": targeted_evaluation_doc,
        }
    )
    normalized["case_contract_valid"] = not normalized["reason_codes"]
    normalized["independent_match_lineage_digest"] = _independent_match_lineage_digest(
        normalized["source_digests"], comparison
    )
    case_contract = {
        "schema_version": CASE_SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {
            "name": CASE_ALGORITHM_NAME,
            "version": CASE_ALGORITHM_VERSION,
            "parameters": {},
        },
        "case": normalized,
        "production_artifact_comparison": comparison,
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_assignments": 0,
            "production_identity_unchanged_derived_from_artifacts": True,
        },
    }
    case_contract["case"] = _case_from_contract(case_contract)
    return case_contract


def build_identity_jersey_number_heldout_validation(
    cases: list[dict[str, Any]],
    *,
    minimum_distinct_source_matches: int = 2,
    minimum_positive_multi_tracklet_propagations: int = 2,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Aggregate N5.8 shadow results without treating clips as separate matches."""
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    normalized_cases = [_evaluate_case(row) for row in cases]
    heldout_cases = [row for row in normalized_cases if row["held_out"]]
    valid_cases = [
        row for row in heldout_cases
        if row.get("canonical_case_origin") is True and row.get("case_contract_valid") is True
    ]
    source_match_keys = sorted(
        {
            str(row["source_match_key"])
            for row in valid_cases
            if row.get("source_match_key")
        }
    )
    lineage_cases: dict[str, dict[str, Any]] = {}
    duplicate_lineages = 0
    lineage_sources: dict[str, set[str]] = {}
    for row in valid_cases:
        lineage = str(row.get("independent_match_lineage_digest") or "")
        if not lineage:
            continue
        if lineage in lineage_cases:
            duplicate_lineages += 1
        else:
            lineage_cases[lineage] = row
        lineage_sources.setdefault(lineage, set()).add(str(row.get("source_match_key") or ""))
    false_assignments = sum(row["identity_false_assignments"] for row in lineage_cases.values())
    false_number_reads = sum(row["false_number_reads"] for row in lineage_cases.values())
    unexpected_targets = sum(row["unexpected_propagated_tracklets"] for row in lineage_cases.values())
    positive_propagations = sum(row["positive_multi_tracklet_propagations"] for row in lineage_cases.values())
    automatic_assignments = sum(row["automatic_assignments"] for row in lineage_cases.values())
    invalid_cases = sum(not row["case_contract_valid"] for row in heldout_cases)

    reason_codes: list[str] = []
    if len(source_match_keys) < minimum_distinct_source_matches:
        reason_codes.append("insufficient_external_match_coverage")
    if len(lineage_cases) < minimum_distinct_source_matches:
        reason_codes.append("insufficient_independent_match_lineage_coverage")
    if duplicate_lineages:
        reason_codes.append("duplicate_independent_match_lineage")
    if any(len(source_keys) > 1 for source_keys in lineage_sources.values()):
        reason_codes.append("reused_independent_match_lineage_across_source_keys")
    if positive_propagations < minimum_positive_multi_tracklet_propagations:
        reason_codes.append("insufficient_positive_multi_tracklet_propagations")
    if false_assignments:
        reason_codes.append("identity_false_assignments_detected")
    if false_number_reads:
        reason_codes.append("false_jersey_number_reads_detected")
    if unexpected_targets:
        reason_codes.append("unexpected_propagated_targets_detected")
    if automatic_assignments:
        reason_codes.append("automatic_assignments_detected")
    if invalid_cases:
        reason_codes.append("heldout_case_contract_incomplete")
    if not heldout_cases:
        reason_codes.append("heldout_cases_missing")

    gate_passed = not reason_codes
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow",
        "status": "passed" if gate_passed else "blocked",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": {
                "minimum_distinct_source_matches": minimum_distinct_source_matches,
                "minimum_positive_multi_tracklet_propagations": (
                    minimum_positive_multi_tracklet_propagations
                ),
            },
        },
        "summary": {
            "activation_gate_passed": gate_passed,
            "cases": len(normalized_cases),
            "heldout_cases": len(heldout_cases),
            "distinct_source_matches": len(source_match_keys),
            "source_match_keys": source_match_keys,
            "distinct_independent_match_lineages": len(lineage_cases),
            "duplicate_independent_match_lineages": duplicate_lineages,
            "positive_multi_tracklet_propagations": positive_propagations,
            "identity_false_assignments": false_assignments,
            "false_number_reads": false_number_reads,
            "unexpected_propagated_tracklets": unexpected_targets,
            "automatic_assignments": automatic_assignments,
            "invalid_cases": invalid_cases,
            "reason_codes": reason_codes,
        },
        "safety": {
            "clips_count_as_distinct_matches": False,
            "requires_distinct_source_match_key": True,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_assignments": 0,
        },
        "cases": normalized_cases,
    }


def _evaluate_case(case: dict[str, Any]) -> dict[str, Any]:
    contract = case.get("case_contract_doc")
    if isinstance(contract, dict):
        return _case_from_contract(contract)
    recognizer = case.get("recognizer_doc") or {}
    assignment = case.get("assignment_doc") or {}
    propagation = case.get("propagation_doc") or {}
    targeted = case.get("targeted_evaluation_doc") or {}
    calibration = recognizer.get("calibration") or {}
    assignment_gate = (assignment.get("safety") or {}).get("benchmark_gate") or {}
    propagation_summary = propagation.get("summary") or {}
    targeted_summary = targeted.get("summary") or {}
    required_documents_present = all(
        isinstance(case.get(name), dict) and bool(case.get(name))
        for name in (
            "recognizer_doc",
            "assignment_doc",
            "propagation_doc",
            "targeted_evaluation_doc",
        )
    )
    production_unchanged = case.get("production_identity_unchanged") is True
    case_reason_codes: list[str] = []
    if not case.get("source_match_key"):
        case_reason_codes.append("source_match_key_missing")
    if not required_documents_present:
        case_reason_codes.append("required_shadow_documents_missing")
    if not production_unchanged:
        case_reason_codes.append("production_identity_unchanged_not_verified")
    if calibration.get("calibration_status") != "measured":
        case_reason_codes.append("recognizer_calibration_not_measured")
    if assignment_gate.get("passed") is not True:
        case_reason_codes.append("assignment_benchmark_gate_failed")
    if targeted_summary.get("safety_passed") is not True:
        case_reason_codes.append("targeted_evaluation_failed")

    false_number_reads = int(
        calibration.get("total_false_confirmed_reads")
        if calibration.get("total_false_confirmed_reads") is not None
        else (
            int(calibration.get("numbered_player_false_positives") or 0)
            + int(calibration.get("false_number_on_plain_shirt") or 0)
        )
    )
    identity_false_assignments = int(assignment_gate.get("identity_false_assignments") or 0)
    unexpected_targets = int(targeted_summary.get("unexpected_propagated_tracklets") or 0)
    positive_propagations = int(
        targeted_summary.get("eligible_matched_hidden_target_tracklets")
        or propagation_summary.get("number_propagated_tracklets")
        or 0
    )
    automatic_assignments = max(
        int(propagation_summary.get("automatic_assignments") or 0),
        int(targeted_summary.get("automatic_assignments") or 0),
    )
    return {
        "benchmark_id": str(case.get("benchmark_id") or "unknown"),
        "source_match_key": case.get("source_match_key"),
        "held_out": bool(case.get("held_out")),
        "canonical_case_origin": False,
        "case_contract_valid": False,
        "production_identity_unchanged": production_unchanged,
        "positive_multi_tracklet_propagations": positive_propagations,
        "identity_false_assignments": identity_false_assignments,
        "false_number_reads": false_number_reads,
        "unexpected_propagated_tracklets": unexpected_targets,
        "automatic_assignments": automatic_assignments,
        "reason_codes": case_reason_codes,
        "source_digests": {
            name.removesuffix("_doc"): canonical_digest(case[name])
            for name in (
                "recognizer_doc",
                "assignment_doc",
                "propagation_doc",
                "targeted_evaluation_doc",
            )
            if isinstance(case.get(name), dict) and case.get(name)
        },
    }


def _independent_match_lineage_digest(
    source_digests: dict[str, Any],
    comparison: dict[str, Any],
) -> str | None:
    required_sources = ("recognizer", "assignment", "propagation", "targeted_evaluation")
    values = {name: str(source_digests.get(name) or "").strip() for name in required_sources}
    artifacts = {
        str(row.get("artifact")): {
            "before_sha256": row.get("before_sha256"),
            "after_sha256": row.get("after_sha256"),
        }
        for row in comparison.get("artifacts") or []
        if isinstance(row, dict) and row.get("artifact") in REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS
    }
    if not all(values.values()) or set(artifacts) != set(REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS):
        return None
    if any(
        not pair["before_sha256"]
        or not pair["after_sha256"]
        or pair["before_sha256"] != pair["after_sha256"]
        for pair in artifacts.values()
    ):
        return None
    return canonical_digest({"source_digests": values, "production_artifact_sha_pairs": artifacts})


def _case_from_contract(contract: dict[str, Any]) -> dict[str, Any]:
    algorithm_value = contract.get("algorithm")
    algorithm = algorithm_value if isinstance(algorithm_value, dict) else {}
    row_value = contract.get("case")
    comparison_value = contract.get("production_artifact_comparison")
    safety_value = contract.get("safety")
    row = row_value if isinstance(row_value, dict) else {}
    comparison = comparison_value if isinstance(comparison_value, dict) else {}
    safety = safety_value if isinstance(safety_value, dict) else {}
    required_fields = (
        "benchmark_id",
        "source_match_key",
        "held_out",
        "canonical_case_origin",
        "case_contract_valid",
        "production_identity_unchanged",
        "positive_multi_tracklet_propagations",
        "identity_false_assignments",
        "false_number_reads",
        "unexpected_propagated_tracklets",
        "automatic_assignments",
        "reason_codes",
        "source_digests",
        "independent_match_lineage_digest",
    )
    required_comparison_fields = {
        "production_identity_unchanged",
        "required_artifacts",
        "missing_required_artifacts",
        "changed_required_artifacts",
        "artifacts",
    }
    required_artifacts = set(REQUIRED_PRODUCTION_IDENTITY_ARTIFACTS)
    comparison_required_artifacts = set(comparison.get("required_artifacts") or [])
    comparison_artifact_names = {
        str(item.get("artifact"))
        for item in comparison.get("artifacts") or []
        if isinstance(item, dict) and item.get("artifact")
    }
    comparison_proves_unchanged = (
        isinstance(comparison, dict)
        and required_comparison_fields.issubset(comparison)
        and required_artifacts.issubset(comparison_required_artifacts)
        and required_artifacts.issubset(comparison_artifact_names)
        and comparison.get("production_identity_unchanged") is True
        and not comparison.get("missing_required_artifacts")
        and not comparison.get("changed_required_artifacts")
        and all(
            isinstance(item, dict)
            and (
                not item.get("required")
                or (
                    item.get("present_before") is True
                    and item.get("present_after") is True
                    and item.get("equal") is True
                )
            )
            for item in comparison.get("artifacts") or []
        )
    )
    safety_valid = (
        isinstance(safety, dict)
        and safety.get("mutates_candidate_identity") is False
        and safety.get("mutates_production_identity") is False
        and int(safety.get("automatic_assignments") or 0) == 0
        and safety.get("production_identity_unchanged_derived_from_artifacts") is True
    )
    contract_valid = (
        contract.get("schema_version") == CASE_SCHEMA_VERSION
        and algorithm.get("name") == CASE_ALGORITHM_NAME
        and algorithm.get("version") == CASE_ALGORITHM_VERSION
        and isinstance(row_value, dict)
        and set(required_fields).issubset(row)
        and safety_valid
        and row.get("case_contract_valid") is True
        and row.get("production_identity_unchanged") is True
        and not set(row.get("reason_codes") or []) & {
            "source_match_key_missing",
            "required_shadow_documents_missing",
            "production_identity_unchanged_not_verified",
            "recognizer_calibration_not_measured",
            "assignment_benchmark_gate_failed",
            "targeted_evaluation_failed",
            "production_identity_artifact_comparison_invalid",
        }
        and row.get("production_identity_unchanged")
        == comparison.get("production_identity_unchanged")
        and row.get("independent_match_lineage_digest")
        == _independent_match_lineage_digest(row.get("source_digests") or {}, comparison)
    )
    if not contract_valid:
        invalid_reasons = {
            "heldout_case_contract_invalid",
            *(str(reason) for reason in row.get("reason_codes") or []),
        }
        if not comparison_proves_unchanged:
            invalid_reasons.add("production_identity_artifact_comparison_invalid")
        return {
            "benchmark_id": str(row.get("benchmark_id") or "unknown"),
            "source_match_key": row.get("source_match_key"),
            "held_out": bool(row.get("held_out")),
            "canonical_case_origin": False,
            "case_contract_valid": False,
            "production_identity_unchanged": False,
            "positive_multi_tracklet_propagations": 0,
            "identity_false_assignments": 0,
            "false_number_reads": 0,
            "unexpected_propagated_tracklets": 0,
            "automatic_assignments": 0,
            "reason_codes": sorted(invalid_reasons),
            "source_digests": {},
            "independent_match_lineage_digest": None,
        }
    normalized = {name: row[name] for name in required_fields}
    if normalized["production_identity_unchanged"] and not comparison_proves_unchanged:
        normalized["case_contract_valid"] = False
        normalized["production_identity_unchanged"] = False
        normalized["reason_codes"] = sorted(
            {
                *(normalized.get("reason_codes") or []),
                "production_identity_artifact_comparison_invalid",
            }
        )
    normalized["benchmark_id"] = str(normalized["benchmark_id"] or "unknown")
    normalized["held_out"] = bool(normalized["held_out"])
    normalized["canonical_case_origin"] = True
    normalized["case_contract_valid"] = bool(normalized["case_contract_valid"])
    normalized["production_identity_unchanged"] = bool(
        normalized["production_identity_unchanged"]
    )
    for name in (
        "positive_multi_tracklet_propagations",
        "identity_false_assignments",
        "false_number_reads",
        "unexpected_propagated_tracklets",
        "automatic_assignments",
    ):
        normalized[name] = int(normalized[name] or 0)
    normalized["reason_codes"] = list(normalized["reason_codes"] or [])
    normalized["source_digests"] = dict(normalized["source_digests"] or {})
    return normalized
