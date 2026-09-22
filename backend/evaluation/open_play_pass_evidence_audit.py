"""Read-only forensics for the current automatic open-play pass pipeline.

This module intentionally lives outside runtime pass generation.  It reuses the
immutable active-play mask from the v2 evaluation, then labels regenerated v1
candidates only after generation for measurement against the manual goldset.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from app.services.pass_candidates import _skip_reason, _sorted_contact_events
from evaluation.contact_action_baseline import (
    PASS_OUTCOMES,
    _aggregate_fidelity,
    _candidate_actor_team,
    _candidate_broad_outcome,
    _classify_pass_false_positives,
    _gold_public,
    _gold_team,
    _match_passes,
    _project_candidates,
    _scope_candidates,
    _score_pass_matches,
)
from evaluation.open_play_pass_v2 import _in_active_play, _window_for_time, derive_active_play_mask


SCHEMA_VERSION = "open-play-pass-evidence-audit:v1"
FALSE_POSITIVE_CLUSTER_GAP_SEC = 1.0
NUMERIC_FEATURES = (
    "duration_sec",
    "distance_m",
    "displacement_m",
    "confidence",
    "release.duration_sec",
    "release.source_clearance_m",
    "release.immediate_contested_frames",
    "release.controlled_by_source_frames",
    "release.controlled_by_target_frames",
    "trajectory.sampled_frames",
    "trajectory.ball_path_distance_m",
    "trajectory.ball_displacement_m",
    "trajectory.mean_ball_speed_mps",
    "trajectory.ball_path_straightness",
    "receiver.min_distance_m",
    "receiver.confidence",
    "context.nearby_contact_count",
    "context.prior_gap_sec",
    "context.next_gap_sec",
    "context.rapid_neighbor_gaps",
    "derived.free_frame_count",
    "derived.free_frame_ratio",
    "derived.contested_frame_ratio",
    "derived.controlled_frame_ratio",
    "derived.source_control_ratio",
    "derived.target_control_ratio",
    "derived.unknown_frame_ratio",
    "derived.source_release_to_target_gap_sec",
    "derived.displacement_to_path_ratio",
)
FALSE_POSITIVE_LABELS = (
    "SHOT_AS_PASS",
    "INTERVENTION_AS_PASS",
    "CONTROL_AS_PASS",
    "CONTEST_AS_PASS",
    "AMBIGUOUS_ACTION_AS_PASS",
    "UNMATCHED_PASS_CANDIDATE",
)


def audit_open_play_pass_evidence(
    goldset: Mapping[str, Any],
    source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    """Build an evaluation-only evidence dataset from current v1 documents."""

    windows = _windows(goldset)
    events = [dict(row) for row in goldset.get("events") or [] if isinstance(row, Mapping)]
    mask = derive_active_play_mask(goldset)
    active_gold = [
        row for row in events
        if _strict_open_play_pass(row)
        and _in_active_play(float(row.get("approx_time_sec") or 0.0), str(row.get("window_id") or ""), mask)
    ]
    projected = _project_candidates(windows, source_documents)
    scoped = _scope_candidates(windows, projected)
    all_active_pass_pairs = [
        row for row in scoped["pass"]
        if not row.get("from_restart")
        and _in_active_play(_number(row.get("merged_release_time_sec")), _window_for_time(windows, _number(row.get("merged_release_time_sec"))), mask)
    ]
    candidates = [row for row in all_active_pass_pairs if str(row.get("outcome")) in PASS_OUTCOMES]
    pairs, missing_indexes, unmatched_indexes = _match_passes(active_gold, candidates)
    match_rows = _score_pass_matches(active_gold, candidates, pairs, [])
    matches_by_candidate = {(_candidate_ref(candidates[candidate_index])): match_rows[index] for index, (_, candidate_index) in enumerate(pairs)}
    false_positive_rows = _classify_pass_false_positives([candidates[index] for index in unmatched_indexes], events, [])
    fp_by_candidate = {_candidate_ref(row): row for row in false_positive_rows}

    evidence_rows = [
        _evidence_row(candidate, "TRUE_PASS_MATCH", matches_by_candidate.get(_candidate_ref(candidate)))
        if _candidate_ref(candidate) in matches_by_candidate
        else _evidence_row(candidate, str(fp_by_candidate[_candidate_ref(candidate)]["classification"]), None)
        for candidate in candidates
    ]
    gold_by_id = {str(row.get("event_id")): row for row in active_gold}
    for row in evidence_rows:
        row["window_id"] = _window_for_time(windows, _number(row.get("merged_release_time_sec")))
        if row.get("matched_gold_event_id"):
            row["gold_subgroups"] = _gold_subgroups(gold_by_id.get(str(row["matched_gold_event_id"])))
    false_positive_clusters = _false_positive_clusters(evidence_rows)
    false_cluster_by_ref = {
        reference: cluster
        for cluster in false_positive_clusters
        for reference in cluster["candidate_refs"]
    }
    all_candidate_clusters = _all_candidate_clusters(evidence_rows)
    all_candidate_cluster_summary = _all_candidate_cluster_summary(all_candidate_clusters)
    false_positive_roots = _false_positive_root_causes(evidence_rows, fp_by_candidate, false_cluster_by_ref)
    for row in evidence_rows:
        root = false_positive_roots.get(row["candidate_ref"])
        if root:
            row["false_positive_root_cause"] = root["root_cause"]
            row["false_positive_cluster_id"] = root.get("cluster_id")

    skipped_pairs = _skipped_contact_pairs(windows, source_documents)
    missed = _missed_gold_pass_root_causes(
        [active_gold[index] for index in sorted(missing_indexes)],
        scoped,
        all_active_pass_pairs,
        skipped_pairs,
    )
    distributions = _feature_distributions(evidence_rows)
    categorical_distributions = _mapping(distributions.pop("categorical", {}))
    team_bias = _team_bias(active_gold, match_rows, evidence_rows, missing_indexes)
    completion_bias = _completion_bias(active_gold, candidates, match_rows, evidence_rows)
    oracles = _oracle_diagnostics(active_gold, candidates, match_rows, evidence_rows)
    hypotheses = _candidate_v3_hypotheses(
        evidence_rows, missed, all_candidate_clusters, all_candidate_cluster_summary, distributions,
    )
    conclusion = "EVIDENCE_INCONCLUSIVE"
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_layer": {
            "active_play_mask": "exactly the #171 evaluation-only mask and boundary contract",
            "candidate_generation": "current pass policy v1 regenerated in memory",
            "production_artifacts_written": False,
            "labels_runtime_visible": False,
            "manual_review_status_used": False,
        },
        "active_play_mask": mask,
        "current_error_budget": {
            "gold_open_play_passes": len(active_gold),
            "matched": len(match_rows),
            "missed": len(missing_indexes),
            "false_positives": len(false_positive_rows),
        },
        "evidence_rows": evidence_rows,
        "feature_distributions": distributions,
        "categorical_distributions": categorical_distributions,
        "missed_gold_pass_root_causes": missed,
        "false_positive_root_causes": list(false_positive_roots.values()),
        "false_positive_clusters": false_positive_clusters,
        "all_candidate_clusters": all_candidate_clusters,
        "all_candidate_cluster_summary": all_candidate_cluster_summary,
        "team_bias_decomposition": team_bias,
        "completion_bias_decomposition": completion_bias,
        "oracle_diagnostics": oracles,
        "candidate_v3_hypotheses": hypotheses,
        "final_conclusion": conclusion,
        "recommended_next_experiment": None,
    }


def render_open_play_pass_evidence_audit_markdown(report: Mapping[str, Any]) -> str:
    """Render the audit with summaries first and raw evidence retained in JSON."""

    budget = _mapping(report.get("current_error_budget"))
    team = _mapping(report.get("team_bias_decomposition"))
    completion = _mapping(report.get("completion_bias_decomposition"))
    lines = [
        "# Open-play Pass Evidence Audit", "",
        "## Executive summary", "",
        "This is a read-only forensic audit of regenerated v1 candidates. Gold labels are applied only after candidate generation and never enter runtime pass statistics.",
        f"- Conclusion: **{report.get('final_conclusion')}**",
        f"- Recommended next experiment: **{report.get('recommended_next_experiment') or 'none'}**", "",
        "## Current error budget", "",
        f"- Gold open-play passes: **{budget.get('gold_open_play_passes', 0)}**",
        f"- Matched / missed / false positives: **{budget.get('matched', 0)} / {budget.get('missed', 0)} / {budget.get('false_positives', 0)}**", "",
        "## Team-share bias decomposition", "",
        f"- Missed by gold team: `{team.get('missed_by_gold_team')}`",
        f"- False positives by automatic team: `{team.get('false_positives_by_automatic_team')}`", "",
        "| Gold actor → automatic actor | Count |", "| --- | ---: |",
    ]
    for key, count in _mapping(team.get("matched_actor_team_confusion_matrix")).items():
        lines.append(f"| {key} | {count} |")
    lines.extend(["", "## Completion bias decomposition", "", "| Gold outcome → automatic outcome | Count |", "| --- | ---: |"])
    for key, count in _mapping(completion.get("matched_outcome_confusion_matrix")).items():
        lines.append(f"| {key} | {count} |")
    lines.extend([
        "", f"- False-positive outcomes: `{completion.get('false_positive_outcomes')}`",
        f"- False-positive outcomes by team: `{completion.get('false_positive_outcomes_by_team')}`", "",
        "## Evidence distributions", "",
        "Raw medians and quantiles below are descriptive only. They are intentionally not ranked across features because their units differ (frames, metres, seconds and ratios).",
        "",
    ])
    for feature, groups in _mapping(report.get("feature_distributions")).items():
        lines.append(f"### {feature}")
        lines.append("")
        lines.extend(["| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"])
        for group, stats in _mapping(groups).items():
            lines.append(_distribution_line(group, _mapping(stats)))
        lines.append("")
    lines.extend(["## Categorical evidence", ""])
    for name, groups in _mapping(report.get("categorical_distributions")).items():
        lines.extend([f"### {name}", "", "| Group | Value | Count | Proportion |", "| --- | --- | ---: | ---: |"])
        for group, values in _mapping(groups).items():
            for value, stats in _mapping(values).items():
                lines.append(f"| {group} | {value} | {_mapping(stats).get('count', 0)} | {_percent(_mapping(stats).get('proportion'))} |")
        lines.append("")
    lines.extend(["", "## Missed gold passes — conservative attribution", ""])
    for row in report.get("missed_gold_pass_root_causes") or []:
        if not isinstance(row, Mapping):
            continue
        lines.append(
            f"- `{row.get('gold_event_id')}` @ {_display(row.get('gold_time_sec'))}s · **{row.get('root_cause')}** · "
            f"contacts `{row.get('nearby_contact_ids')}` · pairs `{row.get('nearby_pass_candidate_ids')}` · "
            f"rejections `{row.get('rejection_reasons')}` · signals `{row.get('diagnostic_signals', [])}`"
        )
    lines.extend(["", "## False-positive root causes", ""])
    root_counts = Counter(str(row.get("root_cause")) for row in report.get("false_positive_root_causes") or [] if isinstance(row, Mapping))
    for reason, count in sorted(root_counts.items()):
        lines.append(f"- `{reason}`: **{count}**")
    signal_counts = Counter(
        signal
        for row in report.get("false_positive_root_causes") or []
        if isinstance(row, Mapping)
        for signal in row.get("diagnostic_signals") or []
    )
    lines.append("- Diagnostic signals below are observations, not asserted causes:")
    for signal, count in sorted(signal_counts.items()):
        lines.append(f"  - `{signal}`: **{count}**")
    lines.extend(["", "## False-positive-only diagnostic clusters", ""])
    clusters = report.get("false_positive_clusters") or []
    sizes = Counter(int(row.get("size") or 0) for row in clusters if isinstance(row, Mapping))
    lines.append(f"- Diagnostic clustering gap: **{FALSE_POSITIVE_CLUSTER_GAP_SEC:.1f}s** within one physical source match.")
    lines.append(f"- False-positive candidates / clusters: **{budget.get('false_positives', 0)} / {len(clusters)}**")
    lines.append(f"- Cluster size distribution: `{dict(sorted(sizes.items()))}`")
    for row in clusters:
        if isinstance(row, Mapping) and int(row.get("size") or 0) > 1:
            lines.append(f"- `{row.get('cluster_id')}` · {row.get('source_match_id')} · {row.get('start_time_sec')}–{row.get('end_time_sec')}s · size {row.get('size')} · `{row.get('candidate_ids')}`")
    all_summary = _mapping(report.get("all_candidate_cluster_summary"))
    lines.extend(["", "## All-candidate cluster composition", ""])
    lines.append(f"- Same documented adjacency gap: **{FALSE_POSITIVE_CLUSTER_GAP_SEC:.1f}s** within one physical source match.")
    lines.append(
        "- Multi-candidate clusters: **{total}** · TRUE_ONLY: **{true_only}** · FALSE_ONLY: **{false_only}** · MIXED: **{mixed}** · "
        "true candidates inside: **{true_candidates}** · false candidates inside: **{false_candidates}**.".format(
            total=all_summary.get("multi_candidate_clusters", 0),
            true_only=all_summary.get("TRUE_ONLY", 0),
            false_only=all_summary.get("FALSE_ONLY", 0),
            mixed=all_summary.get("MIXED", 0),
            true_candidates=all_summary.get("true_candidates_in_multi_clusters", 0),
            false_candidates=all_summary.get("false_candidates_in_multi_clusters", 0),
        )
    )
    lines.append("- Dense candidate clusters include genuine fast passing, so temporal density alone is not a safe consolidation rule.")
    lines.extend(["", "| Window | Multi clusters | TRUE_ONLY | FALSE_ONLY | MIXED | True candidates | False candidates |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"])
    for window_id, values in _mapping(all_summary.get("by_window")).items():
        item = _mapping(values)
        lines.append(
            f"| {window_id} | {item.get('multi_candidate_clusters', 0)} | {item.get('TRUE_ONLY', 0)} | "
            f"{item.get('FALSE_ONLY', 0)} | {item.get('MIXED', 0)} | {item.get('true_candidates', 0)} | {item.get('false_candidates', 0)} |"
        )
    for row in report.get("all_candidate_clusters") or []:
        if not isinstance(row, Mapping) or int(row.get("size") or 0) <= 1:
            continue
        lines.append(
            f"- `{row.get('cluster_id')}` · {row.get('source_match_id')} · {row.get('start_time_sec')}–{row.get('end_time_sec')}s · "
            f"{row.get('composition')} · true={row.get('true_pass_count')} false={row.get('false_positive_count')} · "
            f"refs `{row.get('candidate_refs')}` · labels `{row.get('labels')}` · identities `{row.get('player_identities')}`"
        )
    lines.extend(["", "## Oracle diagnostics — impossible / evaluation-only", ""])
    for name, result in _mapping(report.get("oracle_diagnostics")).items():
        data = _mapping(result)
        aggregate = _mapping(data.get("aggregate"))
        lines.append(f"### {name}")
        lines.append("")
        lines.append(f"- {data.get('description')}")
        lines.append(f"- Attempts: **{aggregate.get('attempts', 0)}** · Corgi share: **{_percent(_nested(aggregate, ('teams', 'Corgi', 'share')))}** · completion: **{_percent(aggregate.get('completion_rate'))}**")
        lines.append("")
    lines.extend(["## Candidate v3 hypotheses", ""])
    for item in report.get("candidate_v3_hypotheses") or []:
        if not isinstance(item, Mapping):
            continue
        lines.extend([
            f"### {item.get('name')}", "",
            f"- Physical interpretation: {item.get('physical_interpretation')}",
            f"- Runtime evidence: {item.get('runtime_evidence')}",
            f"- Supporting windows: `{item.get('supporting_windows')}`; contradicting windows: `{item.get('contradicting_windows')}`; no-evidence windows: `{item.get('no_evidence_windows')}`",
            f"- Genuine pass types at risk: {item.get('genuine_pass_risk')}",
            f"- Measured support: {item.get('measured_support')}", "",
        ])
    lines.append("The evidence is sufficient to retain candidate observations for a future controlled shadow design, but not to specify or promote a production v3 policy. Production remains Pass Policy v1.")
    lines.append("")
    return "\n".join(lines)


def _windows(goldset: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["window_id"]): dict(row) for row in goldset.get("windows") or [] if isinstance(row, Mapping)}


def _strict_open_play_pass(event: Mapping[str, Any]) -> bool:
    return bool(
        event.get("event_type") != "SHOT_REFERENCE"
        and not event.get("context_only")
        and event.get("action") == "PASS"
        and "AMBIGUOUS" not in (event.get("modifiers") or [])
    )


def _evidence_row(candidate: Mapping[str, Any], label: str, match: Mapping[str, Any] | None) -> dict[str, Any]:
    release, trajectory = _mapping(candidate.get("release_evidence")), _mapping(candidate.get("trajectory_evidence"))
    receiver, context = _mapping(candidate.get("receiver_evidence")), _mapping(candidate.get("local_contact_context"))
    statuses = {str(key): int(value or 0) for key, value in _mapping(trajectory.get("status_counts")).items()}
    sampled = _number(trajectory.get("sampled_frames"))
    duration = _number(release.get("duration_sec"), _number(candidate.get("duration_sec")))
    path = _number(trajectory.get("ball_path_distance_m"))
    displacement = _number(trajectory.get("ball_displacement_m"))
    return {
        "candidate_ref": _candidate_ref(candidate),
        "source_match_id": candidate.get("source_match_id"),
        "candidate_id": candidate.get("candidate_id"),
        "merged_release_time_sec": candidate.get("merged_release_time_sec"),
        "source_contact_event_id": candidate.get("source_event_id"),
        "target_contact_event_id": candidate.get("target_event_id"),
        "source_stable_player_id": candidate.get("from_stable_player_id"),
        "target_stable_player_id": candidate.get("to_stable_player_id"),
        "evaluation_label": label,
        "matched_gold_event_id": match.get("gold_event_id") if match else None,
        "gold_subgroups": _gold_subgroups(match),
        "automatic": {
            "actor_team": _candidate_actor_team(candidate),
            "receiver_team": candidate.get("to_team_name"),
            "outcome": _candidate_broad_outcome(candidate),
            "pass_type": candidate.get("pass_type"),
            "confidence": candidate.get("confidence"),
            "auto_review_status": candidate.get("auto_review_status"),
            "review_status": candidate.get("review_status"),
        },
        "runtime_evidence": {
            "duration_sec": candidate.get("duration_sec"),
            "distance_m": candidate.get("distance_m"),
            "displacement_m": candidate.get("displacement_m"),
            "release_evidence": dict(release),
            "trajectory_evidence": dict(trajectory),
            "receiver_evidence": dict(receiver),
            "local_contact_context": dict(context),
            "rejection_reasons": list(candidate.get("rejection_reasons") or []),
        },
        "derived_evidence": {
            "free_frame_count": statuses.get("free", 0),
            "free_frame_ratio": _ratio(statuses.get("free", 0), sampled),
            "contested_frame_ratio": _ratio(statuses.get("contested", 0), sampled),
            "controlled_frame_ratio": _ratio(statuses.get("controlled", 0), sampled),
            "source_control_ratio": _ratio(_number(release.get("controlled_by_source_frames")), sampled),
            "target_control_ratio": _ratio(_number(release.get("controlled_by_target_frames")), sampled),
            "unknown_frame_ratio": _ratio(statuses.get("unknown", 0), sampled),
            "source_release_to_target_gap_sec": duration,
            "displacement_to_path_ratio": _ratio(displacement, path),
        },
    }


def _gold_subgroups(event: Mapping[str, Any] | None) -> list[str]:
    if not event:
        return []
    values = [str(event.get("outcome"))] if event.get("outcome") else []
    values.extend(str(value) for value in event.get("modifiers") or [] if value in {"ONE_TOUCH", "LONG", "THROUGH_BALL", "HEADER"})
    return values


def _feature_distributions(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    groups: dict[str, list[Mapping[str, Any]]] = {"TRUE_PASS_MATCH": [], "ALL_FALSE_POSITIVES": []}
    for label in FALSE_POSITIVE_LABELS:
        groups[label] = []
    for row in rows:
        label = str(row.get("evaluation_label"))
        if label == "TRUE_PASS_MATCH":
            groups["TRUE_PASS_MATCH"].append(row)
        else:
            groups["ALL_FALSE_POSITIVES"].append(row)
            groups.setdefault(label, []).append(row)
    output = {
        feature: {group: _distribution([_feature_value(row, feature) for row in values]) for group, values in groups.items()}
        for feature in NUMERIC_FEATURES
    }
    output["categorical"] = _categorical_distributions(groups)
    return output


def _categorical_distributions(groups: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Any]:
    categorical = ("pass_type", "auto_review_status", "automatic_outcome", "trajectory_status_counts")
    result: dict[str, Any] = {}
    for name in categorical:
        result[name] = {}
        for group, rows in groups.items():
            counts: Counter[str] = Counter()
            for row in rows:
                if name == "trajectory_status_counts":
                    counts.update({str(key): int(value or 0) for key, value in _mapping(_mapping(row.get("runtime_evidence")).get("trajectory_evidence")).get("status_counts", {}).items()})
                elif name == "automatic_outcome":
                    counts[str(_mapping(row.get("automatic")).get("outcome") or "unknown")] += 1
                else:
                    counts[str(_mapping(row.get("automatic")).get(name) or "unknown")] += 1
            total = sum(counts.values())
            result[name][group] = {key: {"count": count, "proportion": _ratio(count, total)} for key, count in sorted(counts.items())}
    return result


def _distribution(values: Sequence[Any]) -> dict[str, Any]:
    numeric = sorted(float(value) for value in values if _finite_number(value))
    missing = len(values) - len(numeric)
    return {
        "sample_count": len(values), "missing_count": missing, "missing_percent": _ratio(missing, len(values)),
        "min": _round(numeric[0]) if numeric else None,
        "p10": _percentile(numeric, 0.10), "p25": _percentile(numeric, 0.25), "median": _percentile(numeric, 0.50),
        "p75": _percentile(numeric, 0.75), "p90": _percentile(numeric, 0.90),
        "max": _round(numeric[-1]) if numeric else None,
    }


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    """Deterministic linear interpolation on sorted values at index p*(n-1)."""

    if not values:
        return None
    index = (len(values) - 1) * percentile
    lower, upper = int(index), min(int(index) + 1, len(values) - 1)
    return _round(values[lower] + (values[upper] - values[lower]) * (index - lower))


def _feature_value(row: Mapping[str, Any], feature: str) -> Any:
    if feature in {"duration_sec", "distance_m", "displacement_m", "confidence"}:
        return _mapping(row.get("runtime_evidence")).get(feature) if feature != "confidence" else _mapping(row.get("automatic")).get(feature)
    prefix, key = feature.split(".", 1)
    if prefix == "release":
        return _mapping(_mapping(row.get("runtime_evidence")).get("release_evidence")).get(key)
    if prefix == "trajectory":
        return _mapping(_mapping(row.get("runtime_evidence")).get("trajectory_evidence")).get(key)
    if prefix == "receiver":
        return _mapping(_mapping(row.get("runtime_evidence")).get("receiver_evidence")).get(key)
    if prefix == "context":
        return _mapping(_mapping(row.get("runtime_evidence")).get("local_contact_context")).get(key)
    return _mapping(row.get("derived_evidence")).get(key)


def _skipped_contact_pairs(
    windows: Mapping[str, Mapping[str, Any]],
    source_documents: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    offsets = {str(row.get("source_match_id")): float(row.get("source_to_merged_offset_sec") or 0.0) for row in windows.values()}
    output: list[dict[str, Any]] = []
    for source_match_id, documents in source_documents.items():
        events = _sorted_contact_events(dict(_mapping(documents.get("event_candidates"))))
        offset = offsets.get(str(source_match_id), 0.0)
        for source, target in zip(events, events[1:]):
            reason = _skip_reason(source, target)
            if not reason:
                continue
            output.append({
                "source_match_id": source_match_id,
                "source_event_id": source.get("event_id"), "target_event_id": target.get("event_id"),
                "source_stable_player_id": source.get("stable_player_id"),
                "target_stable_player_id": target.get("stable_player_id"),
                "source_time_sec": float(source.get("end_time_sec") or source.get("start_time_sec") or 0.0) + offset,
                "target_time_sec": float(target.get("start_time_sec") or target.get("end_time_sec") or 0.0) + offset,
                "skip_reason": reason,
            })
    return output


def _missed_gold_pass_root_causes(
    missed_gold: Sequence[Mapping[str, Any]],
    scoped: Mapping[str, Sequence[Mapping[str, Any]]],
    all_pass_pairs: Sequence[Mapping[str, Any]],
    skipped_pairs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    contacts = [row for row in scoped.get("event", []) if row.get("event_type") == "ball_contact"]
    output: list[dict[str, Any]] = []
    for gold in missed_gold:
        time, tolerance = _number(gold.get("approx_time_sec")), _number(gold.get("timing_tolerance_sec"), 1.0)
        local_contacts = [row for row in contacts if abs(_number(row.get("merged_start_time_sec")) - time) <= max(tolerance, 1.0)]
        near_pairs = [row for row in all_pass_pairs if abs(_number(row.get("merged_release_time_sec")) - time) <= tolerance]
        near_skips = [row for row in skipped_pairs if abs(_number(row.get("source_time_sec")) - time) <= max(tolerance, 1.0)]
        rejected = [row for row in local_contacts if str(row.get("review_status") or row.get("status") or "") == "rejected"]
        excluded = [row for row in near_pairs if str(row.get("outcome")) == "excluded_non_pass"]
        diagnostic_signals: list[str] = []
        if excluded:
            cause = "EXCLUDED_BY_RELEASE_POLICY"
        elif near_pairs:
            cause = "UNKNOWN"
            diagnostic_signals.append("NEARBY_UNMATCHED_PASS_CANDIDATE")
        elif _has_structural_same_player_skip(near_skips, local_contacts):
            cause = "SAME_PLAYER_SKIP"
        elif rejected and len(rejected) == len(local_contacts):
            cause = "CONTACT_REVIEW_FILTER"
        elif not local_contacts:
            cause = "NO_SOURCE_CONTACT"
        else:
            cause = "UNKNOWN"
            if any(str(row.get("skip_reason")) == "same_player_consecutive_contacts" for row in near_skips):
                diagnostic_signals.append("NEARBY_SAME_PLAYER_SKIP")
            if len(local_contacts) == 1:
                diagnostic_signals.append("SINGLE_NEARBY_CONTACT")
            elif len(local_contacts) >= 3:
                diagnostic_signals.append("MULTIPLE_NEARBY_CONTACTS")
        output.append({
            **_gold_public(gold),
            "root_cause": cause,
            "nearby_contact_ids": [row.get("event_id") or row.get("candidate_id") for row in local_contacts],
            "nearby_contact_times_sec": [_number(row.get("merged_start_time_sec")) for row in local_contacts],
            "nearby_contact_statuses": dict(sorted(Counter(str(row.get("review_status") or row.get("status") or "unknown") for row in local_contacts).items())),
            "nearby_pass_candidate_ids": [row.get("candidate_id") for row in near_pairs],
            "rejection_reasons": {str(row.get("candidate_id")): list(row.get("rejection_reasons") or []) for row in excluded},
            "skipped_contact_pairs": near_skips,
            "diagnostic_signals": diagnostic_signals,
        })
    return output


def _has_structural_same_player_skip(
    skipped_pairs: Sequence[Mapping[str, Any]], local_contacts: Sequence[Mapping[str, Any]],
) -> bool:
    """Require an observable same-player chain into a later distinct contact.

    A nearby skip alone only tells us that the approximate manual anchor shares
    time with a construction decision. It becomes causal evidence only when the
    skipped pair itself is known to be same-player and the available local
    contacts show the later distinct receiver/interceptor transition.
    """

    contacts_by_id = {str(row.get("event_id") or row.get("candidate_id")): row for row in local_contacts}
    for skip in skipped_pairs:
        if str(skip.get("skip_reason")) != "same_player_consecutive_contacts":
            continue
        source_id, target_id = str(skip.get("source_event_id")), str(skip.get("target_event_id"))
        source = contacts_by_id.get(source_id)
        target = contacts_by_id.get(target_id)
        source_player = skip.get("source_stable_player_id") or _mapping(source).get("stable_player_id")
        target_player = skip.get("target_stable_player_id") or _mapping(target).get("stable_player_id")
        if not source_player or source_player != target_player:
            continue
        target_time = _number(skip.get("target_time_sec"))
        for contact in local_contacts:
            contact_player = contact.get("stable_player_id")
            if (
                contact_player
                and contact_player != source_player
                and _number(contact.get("merged_start_time_sec")) >= target_time
            ):
                return True
    return False


def _false_positive_clusters(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Retain the historical FP-only view as a diagnostic subset, not a policy claim."""

    return _candidate_clusters(
        [row for row in rows if row.get("evaluation_label") != "TRUE_PASS_MATCH"],
        cluster_id_prefix="fp-cluster",
    )


def _all_candidate_clusters(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Cluster all evaluated candidates so true rapid passing is visible beside FPs."""

    return _candidate_clusters(rows, cluster_id_prefix="candidate-cluster")


def _candidate_clusters(
    rows: Sequence[Mapping[str, Any]], *, cluster_id_prefix: str,
) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("source_match_id"))].append(row)
    output: list[dict[str, Any]] = []
    for source_match_id, values in sorted(groups.items()):
        cluster: list[Mapping[str, Any]] = []
        for row in sorted(values, key=lambda item: (_number(item.get("merged_release_time_sec")), str(item.get("candidate_id")))):
            if cluster and _number(row.get("merged_release_time_sec")) - _number(cluster[-1].get("merged_release_time_sec")) > FALSE_POSITIVE_CLUSTER_GAP_SEC:
                output.append(_cluster_row(source_match_id, cluster, len(output) + 1, cluster_id_prefix))
                cluster = []
            cluster.append(row)
        if cluster:
            output.append(_cluster_row(source_match_id, cluster, len(output) + 1, cluster_id_prefix))
    return output


def _cluster_row(
    source_match_id: str, rows: Sequence[Mapping[str, Any]], index: int, cluster_id_prefix: str,
) -> dict[str, Any]:
    true_pass_count = sum(row.get("evaluation_label") == "TRUE_PASS_MATCH" for row in rows)
    false_positive_count = len(rows) - true_pass_count
    composition = "TRUE_ONLY" if true_pass_count == len(rows) else "FALSE_ONLY" if not true_pass_count else "MIXED"
    return {
        "cluster_id": f"{cluster_id_prefix}-{index:03d}", "source_match_id": source_match_id,
        "start_time_sec": _round(_number(rows[0].get("merged_release_time_sec"))),
        "end_time_sec": _round(_number(rows[-1].get("merged_release_time_sec"))), "size": len(rows),
        "candidate_refs": [str(row.get("candidate_ref")) for row in rows],
        "candidate_ids": [row.get("candidate_id") for row in rows],
        "timestamps_sec": [_round(_number(row.get("merged_release_time_sec"))) for row in rows],
        "labels": dict(sorted(Counter(str(row.get("evaluation_label")) for row in rows).items())),
        "window_ids": sorted({_window_id_for_row(row) for row in rows if _window_id_for_row(row)}),
        "true_pass_count": true_pass_count,
        "false_positive_count": false_positive_count,
        "composition": composition,
        "player_identities": [
            {
                "candidate_ref": row.get("candidate_ref"),
                "source_stable_player_id": row.get("source_stable_player_id"),
                "target_stable_player_id": row.get("target_stable_player_id"),
            }
            for row in rows
        ],
    }


def _all_candidate_cluster_summary(clusters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    multi = [row for row in clusters if int(row.get("size") or 0) > 1]
    by_window: dict[str, Counter[str]] = {f"W{index}": Counter() for index in range(1, 7)}
    for row in multi:
        for window_id in row.get("window_ids") or []:
            bucket = by_window.setdefault(str(window_id), Counter())
            bucket["multi_candidate_clusters"] += 1
            bucket[str(row.get("composition"))] += 1
            bucket["true_candidates"] += int(row.get("true_pass_count") or 0)
            bucket["false_candidates"] += int(row.get("false_positive_count") or 0)
    composition = Counter(str(row.get("composition")) for row in multi)
    return {
        "multi_candidate_clusters": len(multi),
        "TRUE_ONLY": composition["TRUE_ONLY"],
        "FALSE_ONLY": composition["FALSE_ONLY"],
        "MIXED": composition["MIXED"],
        "true_candidates_in_multi_clusters": sum(int(row.get("true_pass_count") or 0) for row in multi),
        "false_candidates_in_multi_clusters": sum(int(row.get("false_positive_count") or 0) for row in multi),
        "by_window": {
            window_id: {
                "multi_candidate_clusters": values["multi_candidate_clusters"],
                "TRUE_ONLY": values["TRUE_ONLY"],
                "FALSE_ONLY": values["FALSE_ONLY"],
                "MIXED": values["MIXED"],
                "true_candidates": values["true_candidates"],
                "false_candidates": values["false_candidates"],
            }
            for window_id, values in sorted(by_window.items())
        },
    }


def _false_positive_root_causes(
    evidence_rows: Sequence[Mapping[str, Any]],
    false_positives: Mapping[str, Mapping[str, Any]],
    clusters: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for row in evidence_rows:
        if row.get("evaluation_label") == "TRUE_PASS_MATCH":
            continue
        reference = str(row.get("candidate_ref"))
        label = str(row.get("evaluation_label"))
        cluster = _mapping(clusters.get(reference))
        if label != "UNMATCHED_PASS_CANDIDATE":
            cause = label
        else:
            cause = "UNEXPLAINED_FALSE_POSITIVE"
        diagnostic_signals: list[str] = []
        if int(cluster.get("size") or 0) > 1:
            diagnostic_signals.append("FALSE_POSITIVE_MULTI_CANDIDATE_CLUSTER")
        derived = _mapping(row.get("derived_evidence"))
        if _number(derived.get("controlled_frame_ratio")) > 0:
            # Both all 30 matches and all 67 false positives have this signal in
            # the real audit. It is retained as evidence, never called causal.
            diagnostic_signals.append("CONTROLLED_FRAMES_PRESENT")
        if _number(derived.get("contested_frame_ratio")) > 0:
            diagnostic_signals.append("CONTESTED_FRAMES_PRESENT")
        output[reference] = {
            "candidate_ref": reference, "candidate_id": row.get("candidate_id"), "source_match_id": row.get("source_match_id"),
            "merged_release_time_sec": row.get("merged_release_time_sec"), "semantic_label": label,
            "root_cause": cause, "cluster_id": cluster.get("cluster_id"),
            "nearest_gold_event_id": _mapping(false_positives.get(reference)).get("nearest_gold_event_id"),
            "diagnostic_signals": diagnostic_signals,
        }
    return output


def _team_bias(
    gold: Sequence[Mapping[str, Any]], matches: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]], missing: set[int],
) -> dict[str, Any]:
    missed = Counter(_gold_team(gold[index]) or "unknown" for index in missing)
    fps = Counter(str(_mapping(row.get("automatic")).get("actor_team") or "unknown") for row in rows if row.get("evaluation_label") != "TRUE_PASS_MATCH")
    confusion = Counter(
        f"{row.get('expected_actor_team') or 'unknown'} -> {row.get('actual_actor_team') or 'unknown'}"
        for row in matches
    )
    return {
        "missed_by_gold_team": dict(sorted(missed.items())),
        "false_positives_by_automatic_team": dict(sorted(fps.items())),
        "matched_actor_team_confusion_matrix": dict(sorted(confusion.items())),
    }


def _completion_bias(
    gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], matches: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    confusion = Counter(
        f"{row.get('expected_broad_outcome')} -> {row.get('actual_outcome')}"
        for row in matches if row.get("expected_broad_outcome")
    )
    fp_rows = [row for row in rows if row.get("evaluation_label") != "TRUE_PASS_MATCH"]
    outcome = Counter(str(_mapping(row.get("automatic")).get("outcome") or "unknown") for row in fp_rows)
    by_team: dict[str, dict[str, int]] = {}
    for team in ("Corgi", "Verisk", "unknown"):
        by_team[team] = dict(sorted(Counter(
            str(_mapping(row.get("automatic")).get("outcome") or "unknown")
            for row in fp_rows if str(_mapping(row.get("automatic")).get("actor_team") or "unknown") == team
        ).items()))
    return {
        "matched_outcome_confusion_matrix": dict(sorted(confusion.items())),
        "false_positive_outcomes": dict(sorted(outcome.items())),
        "false_positive_outcomes_by_team": by_team,
    }


def _oracle_diagnostics(
    gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], matches: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_ref = {_candidate_ref(candidate): candidate for candidate in candidates}
    expected_by_ref = {
        f"{row.get('candidate_source_match_id')}:{row.get('candidate_id')}": row
        for row in matches
    }
    matched_only = [deepcopy(by_ref[reference]) for reference in sorted(expected_by_ref) if reference in by_ref]
    outcome_corrected = [deepcopy(candidate) for candidate in candidates]
    team_corrected = [deepcopy(candidate) for candidate in candidates]
    both_corrected = [deepcopy(candidate) for candidate in candidates]
    for collection, correct_outcome, correct_team in ((outcome_corrected, True, False), (team_corrected, False, True), (both_corrected, True, True)):
        for candidate in collection:
            match = expected_by_ref.get(_candidate_ref(candidate))
            if not match:
                continue
            if correct_outcome:
                candidate["outcome"] = match.get("expected_broad_outcome")
            if correct_team:
                candidate["from_team_name"] = match.get("expected_actor_team")
    return {
        "A_PERFECT_FALSE_POSITIVE_REMOVAL": _oracle("Keep only currently matched true candidates; recall remains limited by missed gold passes.", gold, matched_only),
        "B_PERFECT_OUTCOME_ON_MATCHED_EVENTS": _oracle("Keep all candidates; substitute correct broad outcome only on matched events.", gold, outcome_corrected),
        "C_PERFECT_TEAM_ATTRIBUTION_ON_MATCHED_EVENTS": _oracle("Keep all candidates; substitute gold actor team only on matched events.", gold, team_corrected),
        "D_PERFECT_MATCHED_EVENTS_KEEP_FALSE_POSITIVES": _oracle("Correct team and broad outcome only for matched events while retaining all false positives.", gold, both_corrected),
    }


def _oracle(description: str, gold: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {"description": description, "aggregate": _aggregate_fidelity(gold, candidates)["automatic"]["combined"]}


def _candidate_v3_hypotheses(
    rows: Sequence[Mapping[str, Any]],
    misses: Sequence[Mapping[str, Any]],
    all_candidate_clusters: Sequence[Mapping[str, Any]],
    cluster_summary: Mapping[str, Any],
    distributions: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Describe only hypotheses that current evidence can support conservatively.

    This report deliberately does not turn an observational cluster correlation
    into an executable v3 policy. The existing v2 experiment already showed why
    a generic dense-contact suppression rule is unsafe for real quick passing.
    """

    hypotheses: list[dict[str, Any]] = []
    multi_clusters = [row for row in all_candidate_clusters if int(row.get("size") or 0) > 1]
    false_only_clusters = [row for row in multi_clusters if row.get("composition") == "FALSE_ONLY"]
    if false_only_clusters:
        supporting_windows = sorted({window for row in false_only_clusters for window in row.get("window_ids") or []})
        true_windows = sorted({window for row in multi_clusters if row.get("true_pass_count") for window in row.get("window_ids") or []})
        no_evidence_windows = sorted({"W1", "W2", "W3", "W4", "W5", "W6"} - set(supporting_windows) - set(true_windows))
        hypotheses.append({
            "name": "Specify an identity-aware consolidation rule before any controlled shadow experiment",
            "physical_interpretation": "Some false-only clusters may be duplicate representations of one physical chain, but rapid genuine passes are also temporally dense.",
            "runtime_evidence": "Existing ordered source/target contact IDs, stable-player identities and candidate timestamps; no gold label would be available at runtime.",
            "why_it_should_remove_false_positives": "Observational only: false-only clusters exist, but a generic time-gap consolidation is unsafe because other dense clusters contain genuine passes.",
            "genuine_pass_risk": "High: #171's generic dense-contact/scramble suppression removed true passes together with false positives.",
            "supporting_windows": supporting_windows,
            "contradicting_windows": [],
            "no_evidence_windows": no_evidence_windows,
            "measured_support": (
                f"all-candidate multi-clusters={cluster_summary.get('multi_candidate_clusters', 0)}; "
                f"FALSE_ONLY={cluster_summary.get('FALSE_ONLY', 0)}; MIXED={cluster_summary.get('MIXED', 0)}; "
                f"TRUE_ONLY={cluster_summary.get('TRUE_ONLY', 0)}; true candidates inside={cluster_summary.get('true_candidates_in_multi_clusters', 0)}; "
                f"false candidates inside={cluster_summary.get('false_candidates_in_multi_clusters', 0)}."
            ),
            "readiness": "INSUFFICIENT_FOR_SHADOW_RULE",
        })
    same_player = [row for row in misses if row.get("root_cause") == "SAME_PLAYER_SKIP"]
    if same_player:
        windows = sorted({str(row.get("window_id")) for row in same_player})
        hypotheses.append({
            "name": "Trace same-player skip chains to a later distinct receiver before discarding a release",
            "physical_interpretation": "The consecutive-contact builder can observe several contacts under one stable identity while a gold pass is anchored in the same sequence.",
            "runtime_evidence": "Existing ordered contact IDs, stable-player IDs, skipped-pair reason and release trajectory evidence.",
            "why_it_should_remove_false_positives": "This is a recall hypothesis rather than a false-positive filter: it targets a dominant documented construction loss without changing contact detection.",
            "genuine_pass_risk": "Most same-player chains are continued control; any shadow rule must require a later distinct receiver and visible free-flight evidence.",
            "supporting_windows": windows,
            "contradicting_windows": [],
            "no_evidence_windows": sorted({"W1", "W2", "W3", "W4", "W5", "W6"} - set(windows)),
            "measured_support": f"{len(same_player)} of {len(misses)} missed gold passes trace to a recorded same_player_consecutive_contacts skip.",
            "readiness": "REQUIRES_CONTROLLED_SHADOW",
        })
    intermediate = [row for row in misses if row.get("root_cause") == "INTERMEDIATE_CONTACT_BREAKS_PAIR"]
    if intermediate:
        windows = sorted({str(row.get("window_id")) for row in intermediate})
        hypotheses.append({
            "name": "Allow a bounded skip over one transient intermediate contact when flight remains continuous",
            "physical_interpretation": "A genuine release can include a transient tracked touch before the intended receiver/interceptor contact.",
            "runtime_evidence": "Existing ordered contact events, pair gaps, and release trajectory evidence.",
            "why_it_should_remove_false_positives": "This is a recall recovery hypothesis, not a false-positive filter; it targets missed passes whose consecutive-pair construction is interrupted.",
            "genuine_pass_risk": "Could join unrelated loose-ball actions; require continuous-flight evidence before any shadow test.",
            "supporting_windows": windows,
            "contradicting_windows": [],
            "no_evidence_windows": sorted({"W1", "W2", "W3", "W4", "W5", "W6"} - set(windows)),
            "measured_support": f"{len(intermediate)} missed gold passes are classified from available contact ordering as intermediate-contact breaks.",
            "readiness": "REQUIRES_CONTROLLED_SHADOW",
        })
    release = _mapping(distributions.get("derived.free_frame_ratio"))
    true_median = _mapping(release.get("TRUE_PASS_MATCH")).get("median")
    fp_median = _mapping(release.get("ALL_FALSE_POSITIVES")).get("median")
    if _finite_number(true_median) and _finite_number(fp_median) and abs(float(true_median) - float(fp_median)) >= 0.1:
        support_windows = _label_windows(rows, "TRUE_PASS_MATCH")
        fp_windows = _label_windows(rows, None)
        hypotheses.append({
            "name": "Require a source-control to free-flight transition before a pass is counted",
            "physical_interpretation": "A pass should exhibit ball travel after source control rather than only continued possession/contact jitter.",
            "runtime_evidence": "Existing possession status counts, source clearance, and trajectory path evidence.",
            "why_it_should_remove_false_positives": "True and false candidate free-flight ratios have a measured median separation in the evidence table.",
            "genuine_pass_risk": "Headers, interceptions, blocks and short passes can have sparse or contested ball evidence.",
            "supporting_windows": support_windows,
            "contradicting_windows": [],
            "no_evidence_windows": [window for window in fp_windows if window not in support_windows],
            "measured_support": f"free_frame_ratio median true={true_median}, false-positive={fp_median}; inspect per-window values before implementation.",
            "readiness": "REQUIRES_CONTROLLED_SHADOW",
        })
    return hypotheses[:3]


def _label_windows(rows: Sequence[Mapping[str, Any]], label: str | None) -> list[str]:
    values = [row for row in rows if (row.get("evaluation_label") != "TRUE_PASS_MATCH" if label is None else row.get("evaluation_label") == label)]
    return sorted({_window_id_for_row(row) for row in values if _window_id_for_row(row)})


def _window_id_for_row(row: Mapping[str, Any]) -> str | None:
    time = _number(row.get("merged_release_time_sec"))
    # The merged W1-W6 ranges are deliberately not duplicated in this module;
    # event labels carry no direct window mapping for candidates. This is only
    # populated by callers that already attach a window id.
    value = row.get("window_id")
    return str(value) if value else None


def _candidate_ref(row: Mapping[str, Any]) -> str:
    return f"{row.get('source_match_id') or row.get('candidate_source_match_id') or 'unknown'}:{row.get('candidate_id') or ''}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _finite_number(value: Any) -> bool:
    try:
        return float(value) == float(value) and abs(float(value)) != float("inf")
    except (TypeError, ValueError):
        return False


def _ratio(numerator: Any, denominator: Any) -> float | None:
    value = _number(denominator)
    return _round(_number(numerator) / value) if value else None


def _round(value: float) -> float:
    return round(float(value), 4)


def _nested(value: Mapping[str, Any], path: Sequence[str]) -> Any:
    current: Any = value
    for key in path:
        current = _mapping(current).get(key)
    return current


def _display(value: Any) -> str:
    return "—" if value is None else str(value)


def _percent(value: Any) -> str:
    return "—" if value is None else f"{float(value) * 100:.2f}%"


def _distribution_line(group: str, values: Mapping[str, Any]) -> str:
    return "| " + " | ".join([group, str(values.get("sample_count", 0)), str(values.get("missing_count", 0)), *[_display(values.get(key)) for key in ("min", "p10", "p25", "median", "p75", "p90", "max")]]) + " |"
