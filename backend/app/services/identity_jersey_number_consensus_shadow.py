from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest, stable_key


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_jersey_number_consensus_shadow"
ALGORITHM_VERSION = "1.0.0"
DEFAULT_PARAMETERS: dict[str, Any] = {
    "minimum_consistent_reads": 3,
    "minimum_frame_separation": 12,
    "minimum_read_confidence": 0.90,
    "minimum_consensus_confidence": 0.90,
}


def build_identity_jersey_number_consensus_shadow(
    evidence_doc: dict[str, Any],
    roster_doc: dict[str, Any],
    *,
    goldset_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    rows = [row for row in evidence_doc.get("evidence") or [] if isinstance(row, dict)]
    tracklets = _group_consensus(rows, "tracklet_id", roster_doc, params)
    subjects = _group_consensus(rows, "candidate_subject_id", roster_doc, params)
    evaluation = _evaluate_goldset(tracklets, subjects, goldset_doc or {})
    status_counts = Counter(row["state"] for row in subjects)
    source = {
        "evidence_digest": canonical_digest(evidence_doc),
        "roster_digest": canonical_digest(roster_doc),
        "goldset_digest": canonical_digest(goldset_doc or {}),
    }
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION, "parameters": params},
        "source": source,
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "automatic_assignments": 0,
            "single_crop_consensus_allowed": False,
        },
        "summary": {
            "tracklet_consensus_rows": len(tracklets),
            "subject_consensus_rows": len(subjects),
            "subject_state_counts": dict(sorted(status_counts.items())),
            "strong_subject_consensus": sum(row["strong_consensus"] for row in subjects),
        },
        "tracklets": tracklets,
        "subjects": subjects,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_quality_report",
        "algorithm": artifact["algorithm"],
        "source": source,
        "summary": artifact["summary"],
        "goldset_evaluation": evaluation,
        "gates": {
            "multiple_independent_reads_required": all(
                row["supporting_reads"] >= int(params["minimum_consistent_reads"])
                for row in subjects if row["strong_consensus"]
            ),
            "conflicting_strong_numbers_block_consensus": all(
                not row["strong_consensus"] for row in subjects if row["state"] == "number_conflict"
            ),
            "same_team_unique_roster_lookup_required": all(
                row.get("roster_match") for row in subjects if row["strong_consensus"]
            ),
            "zero_identity_false_assignments_on_goldset": evaluation.get("identity_false_assignments") == 0
            if evaluation.get("available") else None,
        },
    }
    return {
        "identity_jersey_number_consensus_shadow": artifact,
        "identity_jersey_number_report": report,
    }


def _group_consensus(
    rows: list[dict[str, Any]],
    key_name: str,
    roster_doc: dict[str, Any],
    parameters: dict[str, Any],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = str(row.get(key_name) or "")
        if key:
            groups[key].append(row)
    result: list[dict[str, Any]] = []
    for group_id, group_rows in sorted(groups.items()):
        selected = _independent_reads(group_rows, int(parameters["minimum_frame_separation"]))
        trusted = [
            row for row in selected
            if row.get("state") == "number_confirmed"
            and float(row.get("confidence") or 0.0) >= float(parameters["minimum_read_confidence"])
        ]
        strong_conflicts = [
            row for row in selected
            if row.get("state") == "number_conflict"
            and float(row.get("confidence") or 0.0) >= float(parameters["minimum_read_confidence"])
        ]
        counts = Counter(str(row.get("number")) for row in trusted if row.get("number") is not None)
        consensus_number, support = counts.most_common(1)[0] if counts else (None, 0)
        competing = sum(value for number, value in counts.items() if number != consensus_number)
        labels = Counter(str(row.get("team_label") or "U") for row in selected)
        label = labels.most_common(1)[0][0] if labels else "U"
        lookup = (roster_doc.get("unique_number_lookup") or {}).get(f"{label}:{consensus_number}")
        confidence = _consensus_confidence(trusted, consensus_number, support, competing, strong_conflicts)
        conflict = competing > 0 or bool(strong_conflicts)
        strong = bool(
            consensus_number is not None
            and support >= int(parameters["minimum_consistent_reads"])
            and not conflict
            and lookup
            and confidence >= float(parameters["minimum_consensus_confidence"])
        )
        absent_reads = sum(row.get("state") == "number_absent" for row in selected)
        if conflict:
            state = "number_conflict"
        elif strong:
            state = "number_confirmed"
        elif absent_reads >= int(parameters["minimum_consistent_reads"]) and not counts:
            state = "number_absent"
        else:
            state = "number_unreadable"
        result.append(
            {
                "consensus_key": stable_key("jersey-consensus", {"scope": key_name, "id": group_id}),
                key_name: group_id,
                "scope": "tracklet" if key_name == "tracklet_id" else "candidate_subject",
                "candidate_subject_id": str(group_rows[0].get("candidate_subject_id") or ""),
                "team_label": label,
                "state": state,
                "consensus_number": consensus_number if state in {"number_confirmed", "number_conflict"} else None,
                "consensus_confidence": confidence,
                "strong_consensus": strong,
                "supporting_reads": support,
                "conflicting_reads": competing + len(strong_conflicts),
                "absent_reads": absent_reads,
                "independent_reads": len(selected),
                "unique_supporting_tracklets": len({row.get("tracklet_id") for row in trusted if row.get("tracklet_id")}),
                "first_support_frame": min((int(row["frame"]) for row in trusted), default=None),
                "last_support_frame": max((int(row["frame"]) for row in trusted), default=None),
                "supporting_evidence_keys": [
                    row["evidence_key"] for row in trusted if str(row.get("number")) == consensus_number
                ],
                "conflicting_evidence_keys": [
                    row["evidence_key"] for row in selected
                    if row.get("state") == "number_conflict"
                    or (row.get("number") is not None and str(row.get("number")) != consensus_number)
                ],
                "roster_match": lookup,
                "reason_codes": _reason_codes(strong, conflict, support, lookup, parameters),
            }
        )
    return result


def _independent_reads(rows: list[dict[str, Any]], minimum_gap: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    last_by_tracklet: dict[str, int] = {}
    for row in sorted(rows, key=lambda value: (int(value.get("frame") or 0), -float(value.get("confidence") or 0.0))):
        if not (row.get("quality") or {}).get("eligible"):
            continue
        tracklet = str(row.get("tracklet_id") or "")
        frame = int(row.get("frame") or 0)
        if tracklet in last_by_tracklet and frame - last_by_tracklet[tracklet] < minimum_gap:
            continue
        selected.append(row)
        last_by_tracklet[tracklet] = frame
    return selected


def _consensus_confidence(
    reads: list[dict[str, Any]],
    number: str | None,
    support: int,
    competing: int,
    conflicts: list[dict[str, Any]],
) -> float:
    supporting = [float(row.get("confidence") or 0.0) for row in reads if str(row.get("number")) == number]
    if not supporting:
        return 0.0
    base = sum(supporting) / len(supporting)
    support_factor = min(1.0, support / 3.0)
    conflict_penalty = min(0.75, 0.25 * competing + 0.35 * len(conflicts))
    return round(max(0.0, base * support_factor - conflict_penalty), 4)


def _reason_codes(
    strong: bool,
    conflict: bool,
    support: int,
    lookup: dict[str, Any] | None,
    parameters: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if strong:
        reasons.append("multi_frame_unique_roster_consensus")
    if conflict:
        reasons.append("trusted_number_conflict")
    if support < int(parameters["minimum_consistent_reads"]):
        reasons.append("insufficient_independent_reads")
    if support and not lookup:
        reasons.append("no_unique_same_team_roster_match")
    return sorted(reasons)


def _evaluate_goldset(
    tracklets: list[dict[str, Any]],
    subjects: list[dict[str, Any]],
    goldset: dict[str, Any],
) -> dict[str, Any]:
    expected_rows = goldset.get("subjects") or goldset.get("expected_subjects") or []
    if not expected_rows:
        return {"available": False, "identity_false_assignments": None}
    expected = {
        str(row.get("candidate_subject_id")): str(row.get("jersey_number"))
        for row in expected_rows
        if isinstance(row, dict) and row.get("candidate_subject_id") and row.get("jersey_number") is not None
    }
    predicted = {
        str(row.get("candidate_subject_id")): str(row.get("consensus_number"))
        for row in subjects if row.get("strong_consensus")
    }
    true_positive = sum(predicted.get(key) == value for key, value in expected.items())
    false_assignment = sum(key in expected and expected[key] != value for key, value in predicted.items())
    false_positive = sum(key not in expected for key in predicted)
    missed = sum(key not in predicted for key in expected)
    precision_denominator = true_positive + false_assignment + false_positive
    return {
        "available": True,
        "expected_subjects": len(expected),
        "suggested_subjects": len(predicted),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "missed": missed,
        "identity_false_assignments": false_assignment,
        "precision": round(true_positive / precision_denominator, 6) if precision_denominator else None,
        "recall": round(true_positive / len(expected), 6) if expected else None,
        "tracklet_consensus_rows": len(tracklets),
    }
