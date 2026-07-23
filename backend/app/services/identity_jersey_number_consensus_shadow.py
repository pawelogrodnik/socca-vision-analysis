from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest, stable_key
from app.services.identity_jersey_number_roster import _lookup_key
from app.services.identity_jersey_number_visibility_episodes import (
    attach_jersey_visibility_episode_ids,
    partition_jersey_visibility_episodes,
)


SCHEMA_VERSION = "0.5.0"
ALGORITHM_NAME = "identity_jersey_number_consensus_shadow"
ALGORITHM_VERSION = "1.4.0"
DEFAULT_PARAMETERS: dict[str, Any] = {
    "minimum_consistent_reads": 3,
    "minimum_frame_separation": 12,
    "minimum_read_confidence": 0.90,
    "minimum_consensus_confidence": 0.90,
    "minimum_visibility_episode_gap_frames": 45,
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
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        group_id = str(row.get(key_name) or "")
        if group_id:
            groups[
                (
                    group_id,
                    str(row.get("source_match_key") or "").strip(),
                    str(row.get("source_video_key") or "").strip(),
                    str(row.get("team_id") or "").strip(),
                    str(row.get("team_label") or "U"),
                )
            ].append(row)
    result: list[dict[str, Any]] = []
    for group_scope, group_rows in sorted(groups.items()):
        group_id = group_scope[0]
        selected = _independent_reads(
            group_rows,
            int(parameters["minimum_frame_separation"]),
            int(parameters["minimum_visibility_episode_gap_frames"]),
        )
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
        labels = Counter(str(row.get("team_label") or "U") for row in group_rows)
        label = labels.most_common(1)[0][0] if labels else "U"
        source_match_key, source_video_key, team_id, scope_reason = _consensus_scope(group_rows)
        lookup_candidate = (
            (roster_doc.get("unique_number_lookup") or {}).get(
                _lookup_key(source_match_key, team_id, consensus_number)
            )
            if source_match_key and team_id and consensus_number is not None
            else None
        )
        lookup_scope_reason = (
            "roster_lookup_scope_mismatch"
            if lookup_candidate is not None
            and not _lookup_matches_scope(lookup_candidate, source_match_key, team_id, label)
            else None
        )
        lookup = lookup_candidate if lookup_scope_reason is None else None
        scope_reason = scope_reason or lookup_scope_reason
        confidence = _consensus_confidence(trusted, consensus_number, support, competing, strong_conflicts)
        conflict = competing > 0 or bool(strong_conflicts)
        strong = bool(
            consensus_number is not None
            and support >= int(parameters["minimum_consistent_reads"])
            and not conflict
            and scope_reason is None
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
                "consensus_key": stable_key(
                    "jersey-consensus",
                    {
                        "scope": key_name,
                        "id": group_id,
                        "source_match_key": group_scope[1],
                        "source_video_key": group_scope[2],
                        "team_id": group_scope[3],
                        "team_label": group_scope[4],
                    },
                ),
                key_name: group_id,
                "scope": "tracklet" if key_name == "tracklet_id" else "candidate_subject",
                "candidate_subject_id": str(group_rows[0].get("candidate_subject_id") or ""),
                "team_label": label,
                "source_match_key": source_match_key,
                "source_video_key": source_video_key,
                "team_id": team_id,
                "state": state,
                "consensus_number": consensus_number if state in {"number_confirmed", "number_conflict"} else None,
                "consensus_confidence": confidence,
                "strong_consensus": strong,
                "supporting_reads": support,
                "conflicting_reads": competing + len(strong_conflicts),
                "absent_reads": absent_reads,
                "independent_reads": len(selected),
                "independent_visibility_episodes": len(
                    {row.get("visibility_episode_id") for row in selected}
                ),
                "unique_supporting_tracklets": len({row.get("tracklet_id") for row in trusted if row.get("tracklet_id")}),
                "first_support_frame": min((int(row["frame"]) for row in trusted), default=None),
                "last_support_frame": max((int(row["frame"]) for row in trusted), default=None),
                "supporting_evidence_keys": [
                    row["evidence_key"] for row in trusted if str(row.get("number")) == consensus_number
                ],
                "supporting_visibility_episode_ids": sorted({
                    str(row.get("visibility_episode_id"))
                    for row in trusted if str(row.get("number")) == consensus_number
                }),
                "conflicting_evidence_keys": [
                    row["evidence_key"] for row in selected
                    if row.get("state") == "number_conflict"
                    or (row.get("number") is not None and str(row.get("number")) != consensus_number)
                ],
                "roster_match": lookup,
                "reason_codes": _reason_codes(strong, conflict, support, lookup, scope_reason, parameters),
            }
        )
    return result


def _consensus_scope(
    rows: list[dict[str, Any]],
) -> tuple[str | None, str | None, str | None, str | None]:
    scopes = {
        (
            str(row.get("source_match_key") or "").strip(),
            str(row.get("source_video_key") or "").strip(),
            str(row.get("team_id") or "").strip(),
            str(row.get("team_label") or "U"),
        )
        for row in rows
    }
    if not scopes:
        return None, None, None, "missing_roster_scope"
    if any(not source_match_key or not source_video_key or not team_id for source_match_key, source_video_key, team_id, _ in scopes):
        return None, None, None, "missing_roster_scope"
    if any(label == "U" for _, _, _, label in scopes):
        return None, None, None, "unknown_team_roster_scope"
    if len(scopes) != 1:
        return None, None, None, "mixed_roster_scope"
    source_match_key, source_video_key, team_id, _ = next(iter(scopes))
    return source_match_key, source_video_key, team_id, None


def _lookup_matches_scope(
    lookup: Any,
    source_match_key: str | None,
    team_id: str | None,
    label: str,
) -> bool:
    return isinstance(lookup, dict) and (
        str(lookup.get("source_match_key") or "").strip() == source_match_key
        and str(lookup.get("team_id") or "").strip() == team_id
        and str(lookup.get("team_label") or "U") == label
    )


def _independent_reads(
    rows: list[dict[str, Any]],
    minimum_gap: int,
    episode_gap: int,
) -> list[dict[str, Any]]:
    del minimum_gap
    eligible = [row for row in rows if (row.get("quality") or {}).get("eligible")]
    try:
        attached = attach_jersey_visibility_episode_ids(eligible, episode_gap)
        episodes = partition_jersey_visibility_episodes(attached, episode_gap)
    except ValueError:
        return []
    return [
        max(episode, key=lambda row: (float(row.get("confidence") or 0.0), -int(row.get("frame") or 0)))
        for episode in episodes
    ]


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
    scope_reason: str | None,
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
    if scope_reason:
        reasons.append(scope_reason)
    return sorted(reasons)


def _evaluate_goldset(
    tracklets: list[dict[str, Any]],
    subjects: list[dict[str, Any]],
    goldset: dict[str, Any],
) -> dict[str, Any]:
    expected_rows = goldset.get("subjects") or goldset.get("expected_subjects") or []
    if not expected_rows:
        return {
            "available": False,
            "identity_false_assignments": None,
            "false_positive": None,
            "precision": None,
        }
    reviewed: dict[tuple[str, str, str, str] | str, dict[str, Any]] = {}
    unscoped_expected_rows = 0
    for row in expected_rows:
        if not isinstance(row, dict):
            continue
        subject_key = _scoped_subject_key(row)
        if subject_key is None:
            unscoped_expected_rows += 1
            subject_key = _legacy_subject_key(row)
            if subject_key is None:
                continue
        number = row.get("jersey_number")
        state = str(row.get("expected_state") or row.get("state") or "")
        if number is not None:
            state = "number_confirmed"
        if state in {"number_absent", "no_number"}:
            state = "number_absent"
        elif state in {"number_unreadable", "unreadable", "negative"}:
            state = "number_unreadable"
        elif state != "number_confirmed":
            state = "unreviewed"
        reviewed[subject_key] = {
            "state": state,
            "jersey_number": str(number) if number is not None else None,
        }
    expected = {
        key: str(value["jersey_number"])
        for key, value in reviewed.items()
        if value["state"] == "number_confirmed" and value["jersey_number"] is not None
    }
    reviewed_negatives = {
        key for key, value in reviewed.items() if value["state"] in {"number_absent", "number_unreadable"}
    }
    predicted: dict[tuple[str, str, str, str] | str, str] = {}
    unscoped_predicted_rows = 0
    for row in subjects:
        if not row.get("strong_consensus"):
            continue
        subject_key = _scoped_subject_key(row)
        if subject_key is None:
            unscoped_predicted_rows += 1
            subject_key = _legacy_subject_key(row)
            if subject_key is None:
                continue
        predicted[subject_key] = str(row.get("consensus_number"))
    true_positive = sum(predicted.get(key) == value for key, value in expected.items())
    false_assignment = sum(key in expected and expected[key] != value for key, value in predicted.items())
    false_positive = sum(key in reviewed_negatives for key in predicted)
    outside_reviewed_scope = sum(key not in reviewed for key in predicted)
    missed = sum(key not in predicted for key in expected)
    scope_complete = not unscoped_expected_rows and not unscoped_predicted_rows
    precision_denominator = true_positive + false_assignment + false_positive
    return {
        "available": True,
        "expected_subjects": len(expected),
        "reviewed_subjects": len(reviewed),
        "reviewed_numbered_subjects": len(expected),
        "reviewed_no_number_subjects": sum(
            value["state"] == "number_absent" for value in reviewed.values()
        ),
        "reviewed_unreadable_subjects": sum(
            value["state"] == "number_unreadable" for value in reviewed.values()
        ),
        "reviewed_negative_subjects": len(reviewed_negatives),
        "scoped_subject_identity_available": scope_complete,
        "unscoped_expected_rows": unscoped_expected_rows,
        "unscoped_predicted_rows": unscoped_predicted_rows,
        "heldout_matches": len(goldset.get("heldout_matches") or []),
        "suggested_subjects": len(predicted),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "predictions_outside_reviewed_scope": outside_reviewed_scope,
        "missed": missed,
        "identity_false_assignments": false_assignment if scope_complete else None,
        "precision": round(true_positive / precision_denominator, 6) if scope_complete and precision_denominator else None,
        "recall": round(true_positive / len(expected), 6) if scope_complete and expected else None,
        "tracklet_consensus_rows": len(tracklets),
    }


def _scoped_subject_key(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    source_match_key = str(row.get("source_match_key") or "").strip()
    team_id = str(row.get("team_id") or "").strip()
    team = str(row.get("team_label") or "U")
    subject_id = str(row.get("candidate_subject_id") or "").strip()
    if not source_match_key or not team_id or team == "U" or not subject_id:
        return None
    return source_match_key, team_id, team, subject_id


def _legacy_subject_key(row: dict[str, Any]) -> str | None:
    subject_id = str(row.get("candidate_subject_id") or "").strip()
    return f"legacy:{subject_id}" if subject_id else None
