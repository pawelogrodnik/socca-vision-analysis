"""Read-only Team Attribution v2 comparison for existing Pass Policy v1.

This is deliberately not another pass-policy experiment: it never alters the
contact sequence, candidate key, release time, or release rejection evidence.
It only projects team fields from production canonical identity evidence onto
the already generated v1 candidates.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from evaluation.contact_action_baseline import (
    PASS_OUTCOMES,
    _aggregate_fidelity,
    _candidate_actor_team,
    _candidate_receiver_team,
    _gold_target_team,
    _gold_team,
    _match_passes,
    _project_candidates,
    _scope_candidates,
)
from evaluation.open_play_pass_v2 import _in_active_play, _window_for_time, derive_active_play_mask


SCHEMA_VERSION = "pass-team-attribution-comparison:v2"
TEAM_ATTRIBUTION_V1 = "v1_event_team_fields"
TEAM_ATTRIBUTION_V2 = "v2_canonical_stable_identity_then_event"
KNOWN_TEAM_LABELS = frozenset({"A", "B"})


def evaluate_pass_team_attribution_v2(
    goldset: Mapping[str, Any],
    source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
    global_identity_documents: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare v1 event fields to a canonical identity-backed team projection."""

    windows = _windows(goldset)
    mask = derive_active_play_mask(goldset)
    events = [dict(row) for row in goldset.get("events") or [] if isinstance(row, Mapping)]
    gold = [
        row for row in events
        if _strict_open_play_pass(row)
        and _in_active_play(_number(row.get("approx_time_sec")), str(row.get("window_id") or ""), mask)
    ]
    projected = _scope_candidates(windows, _project_candidates(windows, source_documents))
    v1_candidates = [
        dict(row) for row in projected["pass"]
        if not row.get("from_restart")
        and str(row.get("outcome")) in PASS_OUTCOMES
        and _in_active_play(
            _number(row.get("merged_release_time_sec")),
            _window_for_time(windows, _number(row.get("merged_release_time_sec"))),
            mask,
        )
    ]
    contact_indexes = _contact_indexes(source_documents)
    possession_indexes = _possession_indexes(source_documents)
    identity_indexes = {
        source_id: _canonical_identity_index(document)
        for source_id, document in global_identity_documents.items()
    }
    v2_candidates = [
        _apply_team_attribution_v2(candidate, contact_indexes, identity_indexes)
        for candidate in v1_candidates
    ]
    _assert_candidate_set_identical(v1_candidates, v2_candidates)

    pairs, missing_gold, unmatched_candidates = _match_passes(gold, v1_candidates)
    matched = _matched_comparison_rows(
        gold,
        v1_candidates,
        v2_candidates,
        pairs,
        contact_indexes,
        identity_indexes,
        possession_indexes,
    )
    matched_refs = {row["candidate_ref"] for row in matched}
    changes = _changed_candidates(v1_candidates, v2_candidates, matched)
    false_positive_distribution = {
        "v1": _team_distribution([row for row in v1_candidates if _candidate_ref(row) not in matched_refs]),
        "v2": _team_distribution([row for row in v2_candidates if _candidate_ref(row) not in matched_refs]),
    }
    aggregate = {
        "gold": _aggregate_fidelity(gold, [])["gold"]["combined"],
        "v1": _aggregate_fidelity(gold, v1_candidates)["automatic"]["combined"],
        "v2": _aggregate_fidelity(gold, v2_candidates)["automatic"]["combined"],
    }
    result = {
        "schema_version": SCHEMA_VERSION,
        "evaluation_layer": {
            "pass_policy_version": "v1",
            "team_attribution_versions": [TEAM_ATTRIBUTION_V1, TEAM_ATTRIBUTION_V2],
            "active_play_mask": "exactly the #171 evaluation-only mask and boundary contract",
            "candidate_generation": "current Pass Policy v1 regenerated in memory",
            "gold_used_only_after_generation": True,
            "production_artifacts_written": False,
        },
        "current_attribution_path": {
            "contact": "ball_possession controlled segment copies stable-player team fields",
            "event": "event_candidates copies contact team_label/team_id/team_name unchanged",
            "pass": "pass_candidates derives pass_type, outcome and count_for_team_label from source/target event teams",
            "v2": "canonical global-identity stable subject/player team, with event team only as fallback",
        },
        "active_play_mask": mask,
        "candidate_set": {
            "v1_count": len(v1_candidates),
            "v2_count": len(v2_candidates),
            "candidate_refs_identical": True,
            "release_times_identical": True,
            "unmatched_gold_count": len(missing_gold),
            "unmatched_candidate_count": len(unmatched_candidates),
        },
        "matched_team_attribution": _matched_summary(matched),
        "error_audit": [row for row in matched if not row["v1_actor_correct"] or not row["v1_receiver_correct"]],
        "aggregate": aggregate,
        "team_share_error_pp": _team_share_error(aggregate),
        "regression_matrix": _regression_matrix(matched),
        "attribution_changes": changes,
        "false_positive_team_distribution": false_positive_distribution,
        "outcome_pass_type_side_effects": _outcome_side_effects(matched, changes),
        "identity_vs_team_error_decomposition": _identity_error_decomposition(matched),
    }
    result["decision"] = _decision(result)
    return result


def resolve_contact_team_v2(
    contact_event: Mapping[str, Any], identity_index: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Resolve one contact independently from canonical identity then event fields.

    Stable-player prefixes are never interpreted. A stable player key can be
    used only when it occurs exactly once in the persisted canonical identity
    index. Conflicting canonical keys fail closed to UNKNOWN.
    """

    subject = str(contact_event.get("stable_subject_id") or "")
    player = str(contact_event.get("stable_player_id") or "")
    subject_team = _mapping(identity_index.get("by_subject", {})).get(subject)
    player_team = _mapping(identity_index.get("by_player", {})).get(player)
    canonical_values = [item for item in (subject_team, player_team) if item]
    unique_canonical = {
        _team_key(item) for item in canonical_values if _team_key(item) is not None
    }
    event_team = _event_team(contact_event)
    conflicts: list[str] = []
    if len(unique_canonical) > 1:
        return _unknown_attribution("CONFLICTING_CANONICAL_IDENTITY", ["subject_player_canonical_disagreement"])
    if canonical_values:
        canonical = dict(canonical_values[0])
        if event_team and _team_key(event_team) != _team_key(canonical):
            conflicts.append("event_team_disagrees_with_canonical_identity")
        return {
            **canonical,
            "attribution_source": "canonical_stable_subject" if subject_team else "canonical_unique_stable_player",
            "evidence_strength": "durable",
            "conflicting_sources": conflicts,
        }
    if event_team:
        return {
            **event_team,
            "attribution_source": "event_team_fallback",
            "evidence_strength": "transient_fallback",
            "conflicting_sources": conflicts,
        }
    return _unknown_attribution("NO_TEAM_EVIDENCE", conflicts)


def _apply_team_attribution_v2(
    candidate: Mapping[str, Any],
    contact_indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    identity_indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    output = deepcopy(dict(candidate))
    source_match_id = str(candidate.get("source_match_id") or "")
    contacts = _mapping(contact_indexes.get(source_match_id))
    identities = _mapping(identity_indexes.get(source_match_id))
    source_contact = _mapping(contacts.get(str(candidate.get("source_event_id") or "")))
    target_contact = _mapping(contacts.get(str(candidate.get("target_event_id") or "")))
    source = resolve_contact_team_v2(source_contact or _contact_from_candidate(candidate, "from"), identities)
    target = resolve_contact_team_v2(target_contact or _contact_from_candidate(candidate, "to"), identities)
    _write_team(output, "from", source)
    _write_team(output, "to", target)
    pass_type, outcome = _relationship_outcome(source, target)
    output["pass_type"] = pass_type
    output["outcome"] = outcome
    output["completed"] = outcome == "completed_pass"
    output["failed"] = outcome == "failed_pass"
    output["count_for_team_label"] = source.get("team_label") if outcome in PASS_OUTCOMES else None
    output["team_attribution_version"] = TEAM_ATTRIBUTION_V2
    output["source_team_attribution"] = source
    output["target_team_attribution"] = target
    return output


def _contact_from_candidate(candidate: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    return {
        "stable_player_id": candidate.get(f"{prefix}_stable_player_id"),
        "stable_subject_id": candidate.get(f"{prefix}_stable_subject_id"),
        "team_label": candidate.get(f"{prefix}_team_label"),
        "team_id": candidate.get(f"{prefix}_team_id"),
        "team_name": candidate.get(f"{prefix}_team_name"),
    }


def _write_team(candidate: dict[str, Any], prefix: str, attribution: Mapping[str, Any]) -> None:
    candidate[f"{prefix}_team_label"] = attribution.get("team_label")
    candidate[f"{prefix}_team_id"] = attribution.get("team_id")
    candidate[f"{prefix}_team_name"] = attribution.get("team_name")


def _relationship_outcome(source: Mapping[str, Any], target: Mapping[str, Any]) -> tuple[str, str]:
    source_key, target_key = _team_key(source), _team_key(target)
    if source_key is None or target_key is None:
        return "unknown_team_pass", "unknown_pass_attempt"
    if source_key == target_key:
        return "same_team_pass", "completed_pass"
    return "turnover_or_interception", "failed_pass"


def _canonical_identity_index(document: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    by_subject: dict[str, dict[str, Any]] = {}
    player_values: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for slot in document.get("slots") or []:
        if not isinstance(slot, Mapping):
            continue
        team = _event_team(slot)
        if not team:
            continue
        subject = str(slot.get("stable_subject_id") or slot.get("slot_id") or "")
        player = str(slot.get("stable_player_id") or "")
        if subject:
            by_subject[subject] = team
        if player:
            player_values[player].append(team)
    by_player = {
        player: values[0]
        for player, values in player_values.items()
        if len({_team_key(value) for value in values}) == 1
    }
    return {"by_subject": by_subject, "by_player": by_player}


def _contact_indexes(source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, dict[str, dict[str, Any]]]:
    output: dict[str, dict[str, dict[str, Any]]] = {}
    for source_id, documents in source_documents.items():
        output[str(source_id)] = {
            str(row.get("event_id")): dict(row)
            for row in _mapping(documents.get("event_candidates")).get("events") or []
            if isinstance(row, Mapping) and row.get("event_id")
        }
    return output


def _possession_indexes(source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Index persisted production possession frames for audit-only local evidence.

    These rows are intentionally not an attribution input.  They document what
    the current possession layer observed around an already matched contact.
    """

    return {
        str(source_id): [
            dict(row)
            for row in _mapping(documents.get("possession_candidates")).get("frames") or []
            if isinstance(row, Mapping)
        ]
        for source_id, documents in source_documents.items()
    }


def _matched_comparison_rows(
    gold: Sequence[Mapping[str, Any]],
    v1: Sequence[Mapping[str, Any]],
    v2: Sequence[Mapping[str, Any]],
    pairs: Sequence[tuple[int, int]],
    contact_indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    identity_indexes: Mapping[str, Mapping[str, Mapping[str, Any]]],
    possession_indexes: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gold_index, candidate_index in pairs:
        expected, before, after = gold[gold_index], v1[candidate_index], v2[candidate_index]
        source_id = str(before.get("source_match_id") or "")
        contacts, identities = _mapping(contact_indexes.get(source_id)), _mapping(identity_indexes.get(source_id))
        possession = possession_indexes.get(source_id) or []
        source_contact = _mapping(contacts.get(str(before.get("source_event_id") or "")))
        target_contact = _mapping(contacts.get(str(before.get("target_event_id") or "")))
        expected_actor, expected_receiver = _gold_team(expected), _gold_target_team(expected)
        row = {
            "candidate_ref": _candidate_ref(before), "candidate_id": before.get("candidate_id"),
            "source_match_id": source_id, "window_id": expected.get("window_id"),
            "gold_event_id": expected.get("event_id"), "gold_time_sec": expected.get("approx_time_sec"),
            "gold_actor_team": expected_actor, "gold_receiver_team": expected_receiver,
            "v1_actor_team": _candidate_actor_team(before), "v2_actor_team": _candidate_actor_team(after),
            "v1_receiver_team": _candidate_receiver_team(before), "v2_receiver_team": _candidate_receiver_team(after),
            "v1_pass_type": before.get("pass_type"), "v2_pass_type": after.get("pass_type"),
            "v1_outcome": before.get("outcome"), "v2_outcome": after.get("outcome"),
            "v1_actor_correct": _candidate_actor_team(before) == expected_actor if expected_actor else None,
            "v2_actor_correct": _candidate_actor_team(after) == expected_actor if expected_actor else None,
            "v1_receiver_correct": _candidate_receiver_team(before) == expected_receiver if expected_receiver else None,
            "v2_receiver_correct": _candidate_receiver_team(after) == expected_receiver if expected_receiver else None,
            "source_contact": _contact_evidence(source_contact, identities, possession),
            "target_contact": _contact_evidence(target_contact, identities, possession),
            "source_team_attribution": after.get("source_team_attribution"),
            "target_team_attribution": after.get("target_team_attribution"),
        }
        row["actor_error_cause"] = _team_error_cause(
            expected_actor,
            row["v1_actor_team"],
            row["source_contact"],
            row["source_team_attribution"],
        )
        row["receiver_error_cause"] = _team_error_cause(
            expected_receiver,
            row["v1_receiver_team"],
            row["target_contact"],
            row["target_team_attribution"],
        )
        row["error_decomposition"] = _primary_error_decomposition(row)
        rows.append(row)
    return rows


def _contact_evidence(
    contact: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
    possession_frames: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    stable_subject = str(contact.get("stable_subject_id") or "")
    stable_player = str(contact.get("stable_player_id") or "")
    return {
        "event_id": contact.get("event_id"), "stable_player_id": stable_player or None,
        "stable_subject_id": stable_subject or None,
        "event_team": _event_team(contact),
        "canonical_subject_team": _mapping(identities.get("by_subject")).get(stable_subject),
        "canonical_player_team": _mapping(identities.get("by_player")).get(stable_player),
        "nearby_controlled_possession": _nearby_controlled_possession(contact, possession_frames),
    }


def _nearby_controlled_possession(
    contact: Mapping[str, Any], frames: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return bounded local production evidence without feeding it to V2."""

    start = _number(contact.get("start_time_sec")) - 0.5
    end = _number(contact.get("end_time_sec")) + 0.5
    subject = str(contact.get("stable_subject_id") or "")
    player = str(contact.get("stable_player_id") or "")
    selected = [
        frame
        for frame in frames
        if frame.get("status") == "controlled"
        and start <= _number(frame.get("time_sec")) <= end
        and (
            (subject and str(frame.get("stable_subject_id") or "") == subject)
            or (player and str(frame.get("stable_player_id") or "") == player)
        )
    ]
    labels = Counter(str(frame.get("team_label")) for frame in selected if str(frame.get("team_label")) in KNOWN_TEAM_LABELS)
    return {
        "window_sec": {"start": _round(start), "end": _round(end)},
        "controlled_frame_count": len(selected),
        "team_label_counts": dict(sorted(labels.items())),
        "samples": [
            {"frame": frame.get("frame"), "time_sec": frame.get("time_sec"), "team_label": frame.get("team_label"), "stable_subject_id": frame.get("stable_subject_id")}
            for frame in selected[:5]
        ],
    }


def _team_error_cause(
    expected_team: str | None,
    v1_team: str | None,
    contact_evidence: Mapping[str, Any],
    attribution: Any,
) -> str | None:
    if not expected_team or expected_team == v1_team:
        return None
    if _candidate_actor_team_from_evidence(contact_evidence) == v1_team:
        return "UPSTREAM_IDENTITY_ERROR"
    if _mapping(attribution).get("conflicting_sources"):
        return "CONFLICTING_IDENTITY_EVIDENCE"
    return "UNKNOWN"


def _primary_error_decomposition(row: Mapping[str, Any]) -> str:
    return str(row.get("actor_error_cause") or row.get("receiver_error_cause") or "UNKNOWN")


def _candidate_actor_team_from_evidence(contact: Any) -> str | None:
    canonical = _mapping(contact).get("canonical_subject_team") or _mapping(contact).get("canonical_player_team")
    return _team_display(_mapping(canonical)) if canonical else None


def _matched_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "matched_count": len(rows),
        "v1": _attribution_metrics(rows, "v1"),
        "v2": _attribution_metrics(rows, "v2"),
    }


def _attribution_metrics(rows: Sequence[Mapping[str, Any]], version: str) -> dict[str, Any]:
    actor = [row for row in rows if row.get("gold_actor_team")]
    receiver = [row for row in rows if row.get("gold_receiver_team")]
    actor_values = [row.get(f"{version}_actor_team") for row in actor]
    receiver_values = [row.get(f"{version}_receiver_team") for row in receiver]
    return {
        "actor_accuracy": _ratio(sum(row.get(f"{version}_actor_correct") is True for row in actor), len(actor)),
        "receiver_accuracy": _ratio(sum(row.get(f"{version}_receiver_correct") is True for row in receiver), len(receiver)),
        "actor_confusion_matrix": _confusion(actor, "gold_actor_team", f"{version}_actor_team"),
        "receiver_confusion_matrix": _confusion(receiver, "gold_receiver_team", f"{version}_receiver_team"),
        "unknown_actor_count": sum(value is None for value in actor_values),
        "unknown_receiver_count": sum(value is None for value in receiver_values),
    }


def _confusion(rows: Sequence[Mapping[str, Any]], expected: str, actual: str) -> dict[str, int]:
    return dict(sorted(Counter(f"{row.get(expected) or 'unknown'} -> {row.get(actual) or 'unknown'}" for row in rows).items()))


def _regression_matrix(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    return {kind: _transition_counts(rows, f"v1_{kind}_correct", f"v2_{kind}_correct") for kind in ("actor", "receiver")}


def _transition_counts(rows: Sequence[Mapping[str, Any]], before: str, after: str) -> dict[str, int]:
    output: Counter[str] = Counter()
    for row in rows:
        if row.get(before) is None or row.get(after) is None:
            continue
        output[f"V1_{'correct' if row[before] else 'wrong'}_TO_V2_{'correct' if row[after] else 'wrong'}"] += 1
    return {key: output[key] for key in (
        "V1_correct_TO_V2_correct", "V1_wrong_TO_V2_correct", "V1_correct_TO_V2_wrong", "V1_wrong_TO_V2_wrong",
    )}


def _changed_candidates(v1: Sequence[Mapping[str, Any]], v2: Sequence[Mapping[str, Any]], matched: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    matched_by_ref = {str(row.get("candidate_ref")): row for row in matched}
    rows: list[dict[str, Any]] = []
    for before, after in zip(v1, v2):
        if _team_key(_team_from_candidate(before, "from")) == _team_key(_team_from_candidate(after, "from")) and _team_key(_team_from_candidate(before, "to")) == _team_key(_team_from_candidate(after, "to")):
            continue
        reference = _candidate_ref(before)
        matched_row = _mapping(matched_by_ref.get(reference))
        rows.append({
            "candidate_ref": reference, "candidate_id": before.get("candidate_id"), "source_match_id": before.get("source_match_id"),
            "merged_release_time_sec": before.get("merged_release_time_sec"),
            "source_stable_player_id": before.get("from_stable_player_id"), "target_stable_player_id": before.get("to_stable_player_id"),
            "v1_source_team": _candidate_actor_team(before), "v2_source_team": _candidate_actor_team(after),
            "v1_target_team": _candidate_receiver_team(before), "v2_target_team": _candidate_receiver_team(after),
            "source_attribution": after.get("source_team_attribution"), "target_attribution": after.get("target_team_attribution"),
            "evaluation_label": "TRUE_PASS_MATCH" if matched_row else "FALSE_POSITIVE",
            "change_corrected_gold_error": bool(matched_row and (not matched_row.get("v1_actor_correct") and matched_row.get("v2_actor_correct"))),
            "change_worsened_gold_error": bool(matched_row and (matched_row.get("v1_actor_correct") and not matched_row.get("v2_actor_correct"))),
        })
    return {
        "source_team_changes": sum(row["v1_source_team"] != row["v2_source_team"] for row in rows),
        "target_team_changes": sum(row["v1_target_team"] != row["v2_target_team"] for row in rows),
        "corrected_on_matched_gold": sum(bool(row["change_corrected_gold_error"]) for row in rows),
        "worsened_on_matched_gold": sum(bool(row["change_worsened_gold_error"]) for row in rows),
        "unknown_introduced": sum(
            (row["v2_source_team"] is None and row["v1_source_team"] is not None)
            or (row["v2_target_team"] is None and row["v1_target_team"] is not None)
            for row in rows
        ),
        "rows": rows,
    }


def _outcome_side_effects(matched: Sequence[Mapping[str, Any]], changes: Mapping[str, Any]) -> dict[str, Any]:
    changed = _mapping(changes).get("rows") or []
    return {
        "pass_type_changes": sum(row.get("v1_pass_type") != row.get("v2_pass_type") for row in matched),
        "outcome_changes": sum(row.get("v1_outcome") != row.get("v2_outcome") for row in matched),
        "matched_outcome_corrected": sum(
            row.get("v1_outcome") != row.get("v2_outcome") and row.get("v2_actor_correct") for row in matched
        ),
        "matched_outcome_worsened": sum(
            row.get("v1_outcome") != row.get("v2_outcome") and not row.get("v2_actor_correct") for row in matched
        ),
        "changed_candidate_rows": len(changed),
    }


def _identity_error_decomposition(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for kind in ("actor", "receiver"):
            cause = row.get(f"{kind}_error_cause")
            if cause:
                counts[f"{kind}:{cause}"] += 1
    return dict(sorted(counts.items()))


def _team_distribution(candidates: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    values = Counter(_candidate_actor_team(row) or "unknown" for row in candidates)
    return {team: values[team] for team in ("Corgi", "Verisk", "unknown")}


def _team_share_error(aggregate: Mapping[str, Any]) -> dict[str, dict[str, float | None]]:
    gold, v1, v2 = (_mapping(aggregate.get(key)) for key in ("gold", "v1", "v2"))
    return {
        team: {
            "v1_delta_pp": _round((_number(_nested(v1, ("teams", team, "share"))) - _number(_nested(gold, ("teams", team, "share")))) * 100),
            "v2_delta_pp": _round((_number(_nested(v2, ("teams", team, "share"))) - _number(_nested(gold, ("teams", team, "share")))) * 100),
        }
        for team in ("Corgi", "Verisk")
    }


def _decision(report: Mapping[str, Any]) -> str:
    metrics = _mapping(_mapping(report.get("matched_team_attribution")).get("v2"))
    v1 = _mapping(_mapping(report.get("matched_team_attribution")).get("v1"))
    regression = _mapping(_mapping(report.get("regression_matrix")).get("actor"))
    changes = _mapping(report.get("attribution_changes"))
    if not int(changes.get("source_team_changes") or 0) and not int(changes.get("target_team_changes") or 0) and _number(v1.get("actor_accuracy")) < 1:
        return "UPSTREAM_IDENTITY_BLOCKER"
    if _number(metrics.get("actor_accuracy")) > _number(v1.get("actor_accuracy")) and not int(regression.get("V1_correct_TO_V2_wrong") or 0):
        return "TEAM_V2_CLEAR_IMPROVEMENT"
    if _number(metrics.get("actor_accuracy")) < _number(v1.get("actor_accuracy")):
        return "TEAM_V2_NO_IMPROVEMENT"
    return "TEAM_V2_MIXED"


def render_pass_team_attribution_v2_markdown(report: Mapping[str, Any]) -> str:
    matched = _mapping(report.get("matched_team_attribution"))
    aggregate = _mapping(report.get("aggregate"))
    error = _mapping(report.get("team_share_error_pp"))
    changes = _mapping(report.get("attribution_changes"))
    fp = _mapping(report.get("false_positive_team_distribution"))
    lines = [
        "# Pass Team Attribution v2", "",
        "## Current attribution path", "",
        "- Contact candidates inherit team fields from controlled possession segments built from the production player event timeline.",
        "- Event candidates copy contact `team_label`, `team_id` and `team_name` unchanged.",
        "- Pass Policy v1 derives source/target relationship, completion outcome and `count_for_team_label` from those event teams.",
        "- V2 is evaluation-only here: canonical global-identity slot team is preferred; event team is a fallback; no stable-ID prefix heuristic is used.", "",
        "## Error audit", "",
    ]
    errors = report.get("error_audit") or []
    lines.append(f"- Matched actor/receiver attribution errors audited: **{len(errors)}**.")
    for row in errors:
        source_possession = _mapping(_mapping(row.get("source_contact")).get("nearby_controlled_possession"))
        target_possession = _mapping(_mapping(row.get("target_contact")).get("nearby_controlled_possession"))
        lines.append(
            f"- `{row.get('gold_event_id')}` @ {row.get('gold_time_sec')}s · gold {row.get('gold_actor_team')}→{row.get('gold_receiver_team')} · "
            f"v1 {row.get('v1_actor_team')}→{row.get('v1_receiver_team')} · source `{_mapping(row.get('source_contact')).get('event_id')}` / "
            f"target `{_mapping(row.get('target_contact')).get('event_id')}` · actor `{row.get('actor_error_cause')}` · "
            f"receiver `{row.get('receiver_error_cause')}` · possession frames source `{source_possession.get('team_label_counts')}` / "
            f"target `{target_possession.get('team_label_counts')}`"
        )
    lines.extend(["", "## V1 vs V2 matched attribution", "", "| Metric | V1 | V2 |", "| --- | ---: | ---: |"])
    for key, label in (("actor_accuracy", "Actor accuracy"), ("receiver_accuracy", "Receiver accuracy"), ("unknown_actor_count", "Unknown actor"), ("unknown_receiver_count", "Unknown receiver")):
        lines.append(f"| {label} | {_display_metric(_nested(matched, ('v1', key)))} | {_display_metric(_nested(matched, ('v2', key)))} |")
    for kind in ("actor", "receiver"):
        lines.extend(["", f"## {kind.title()} confusion matrix", ""])
        lines.append(f"- V1: `{_nested(matched, ('v1', f'{kind}_confusion_matrix'))}`")
        lines.append(f"- V2: `{_nested(matched, ('v2', f'{kind}_confusion_matrix'))}`")
    lines.extend(["", "## Aggregate team share", "", "| Metric | Gold | V1 | V2 |", "| --- | ---: | ---: |"])
    for name, path in (("Attempts", ("attempts",)), ("Corgi attempts", ("teams", "Corgi", "attempts")), ("Verisk attempts", ("teams", "Verisk", "attempts")), ("Unknown attempts", ("teams", "unknown", "attempts")), ("Corgi share", ("teams", "Corgi", "share")), ("Verisk share", ("teams", "Verisk", "share")), ("Completion rate", ("completion_rate",))):
        lines.append(f"| {name} | {_display_metric(_nested(aggregate.get('gold'), path))} | {_display_metric(_nested(aggregate.get('v1'), path))} | {_display_metric(_nested(aggregate.get('v2'), path))} |")
    lines.extend([f"- Corgi share delta vs gold: V1 `{_mapping(error.get('Corgi')).get('v1_delta_pp')} pp`; V2 `{_mapping(error.get('Corgi')).get('v2_delta_pp')} pp`.", f"- Verisk share delta vs gold: V1 `{_mapping(error.get('Verisk')).get('v1_delta_pp')} pp`; V2 `{_mapping(error.get('Verisk')).get('v2_delta_pp')} pp`.", "", "## False-positive team distribution", "", f"- V1: `{fp.get('v1')}`", f"- V2: `{fp.get('v2')}`", "", "## Outcome/pass-type side effects", "", f"- `{report.get('outcome_pass_type_side_effects')}`", "", "## Changed candidates", "", f"- Source changes: **{changes.get('source_team_changes', 0)}**; target changes: **{changes.get('target_team_changes', 0)}**; corrected matched: **{changes.get('corrected_on_matched_gold', 0)}**; worsened matched: **{changes.get('worsened_on_matched_gold', 0)}**; unknown introduced: **{changes.get('unknown_introduced', 0)}**."])
    for row in changes.get("rows") or []:
        lines.append(f"- `{row.get('candidate_ref')}` @ {row.get('merged_release_time_sec')}s · {row.get('v1_source_team')}→{row.get('v2_source_team')} / {row.get('v1_target_team')}→{row.get('v2_target_team')} · {row.get('evaluation_label')}")
    lines.extend(["", "## Identity-vs-team-error decomposition", "", f"- `{report.get('identity_vs_team_error_decomposition')}`", "", "## Decision", "", f"**{report.get('decision')}**. Candidate generation remains Pass Policy v1; no pass suppression, thresholds, contacts or restart logic changed.", ""])
    return "\n".join(lines)


def _assert_candidate_set_identical(v1: Sequence[Mapping[str, Any]], v2: Sequence[Mapping[str, Any]]) -> None:
    before = [(_candidate_ref(row), row.get("merged_release_time_sec")) for row in v1]
    after = [(_candidate_ref(row), row.get("merged_release_time_sec")) for row in v2]
    if before != after:
        raise AssertionError("Team attribution must not alter candidate refs or release times")


def _event_team(row: Mapping[str, Any]) -> dict[str, Any] | None:
    label = str(row.get("team_label") or "").upper()
    team_id, name = row.get("team_id"), row.get("team_name")
    if label not in KNOWN_TEAM_LABELS and not team_id and not name:
        return None
    return {"team_label": label if label in KNOWN_TEAM_LABELS else None, "team_id": team_id, "team_name": name}


def _team_from_candidate(row: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    return {"team_label": row.get(f"{prefix}_team_label"), "team_id": row.get(f"{prefix}_team_id"), "team_name": row.get(f"{prefix}_team_name")}


def _team_key(value: Mapping[str, Any] | None) -> str | None:
    row = _mapping(value)
    return str(row.get("team_id") or row.get("team_label") or row.get("team_name") or "") or None


def _team_display(value: Mapping[str, Any]) -> str | None:
    label = str(value.get("team_label") or "")
    return {"A": "Corgi", "B": "Verisk"}.get(label, str(value.get("team_name") or "") or None)


def _unknown_attribution(source: str, conflicts: Sequence[str]) -> dict[str, Any]:
    return {"team_label": None, "team_id": None, "team_name": None, "attribution_source": source, "evidence_strength": "unresolved", "conflicting_sources": list(conflicts)}


def _candidate_ref(row: Mapping[str, Any]) -> str:
    return f"{row.get('source_match_id') or 'unknown'}:{row.get('candidate_id') or ''}"


def _strict_open_play_pass(row: Mapping[str, Any]) -> bool:
    return bool(row.get("event_type") != "SHOT_REFERENCE" and not row.get("context_only") and row.get("action") == "PASS" and "AMBIGUOUS" not in (row.get("modifiers") or []))


def _windows(goldset: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["window_id"]): dict(row) for row in goldset.get("windows") or [] if isinstance(row, Mapping)}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _ratio(numerator: int, denominator: int) -> float | None:
    return _round(numerator / denominator) if denominator else None


def _round(value: float) -> float:
    return round(float(value), 4)


def _nested(value: Any, path: Sequence[str]) -> Any:
    current: Any = value
    for key in path:
        current = _mapping(current).get(key)
    return current


def _display_metric(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float) and 0.0 <= value <= 1.0:
        return f"{value * 100:.2f}%"
    return str(value)
