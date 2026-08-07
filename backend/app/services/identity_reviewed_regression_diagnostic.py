from __future__ import annotations

"""Read-only, evidence-based A/B diagnostics for identity artifacts.

The stable/global artifacts are a comparison baseline, not a claim about the
real-world identity of a footballer.  In particular, candidate-subject
membership is downstream grouping evidence and must never be used as proof
that the stable resolver switched a player.
"""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

from app.services.identity_reviewed_effective_observation import (
    effective_reviewed_observation,
    is_real_detected_position,
    observation_index,
)
from app.services.identity_canonical_ownership import (
    artifact_membership_integrity,
    global_observation_ownership,
    slot_claims,
)


REQUIRED_ARTIFACTS = (
    "match.json",
    "tracklets.json",
    "global_identity.json",
    "stable_players.json",
    "identity_candidate_shadow.json",
    "reviewed_identity_snapshot.json",
)
OPTIONAL_ARTIFACTS = (
    "reviewed_identity_slot_assignments.json",
    "identity_roster_subject_review_decisions_shadow.json",
    "identity_seeded_candidate_assignments.json",
)
DIAGNOSTIC_VERSION = "reviewed_identity_regression_diagnostic:v4"
DEFAULT_CASE_NAMES = ("Mati GK", "Przemek", "Andrzej", "Roman", "Piotrek", "Paweł", "Krzysiek")
_STABLE_SLOT = re.compile(r"^(?P<team>[AB])(?P<number>\d+)(?:~\d+)?$")


def build_reviewed_identity_regression_diagnostic(
    match_path: Path,
    *,
    case_names: tuple[str, ...] = DEFAULT_CASE_NAMES,
) -> dict[str, Any]:
    """Compare frozen artifacts without writing any artifact under ``match_path``."""
    missing = [name for name in REQUIRED_ARTIFACTS if not (match_path / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing required diagnostic artifacts in {match_path}: {', '.join(missing)}"
        )

    before_hashes = _artifact_hashes(match_path)
    documents = {name: _load(match_path / name) for name in REQUIRED_ARTIFACTS}
    optional_documents = {name: _optional(match_path / name) for name in OPTIONAL_ARTIFACTS}
    tracks_document = _optional_mapping(match_path / "tracks.json")
    fps = _fps(documents, tracks_document)
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in documents["tracklets.json"].get("tracklets") or []
        if row.get("tracklet_id")
    }
    global_slots = _slot_claims(
        documents["global_identity.json"], "slots", source="global_identity"
    )
    stable_slots = _slot_claims(
        documents["stable_players.json"], "players", source="stable_players"
    )
    canonical_ownership = {
        (str(row["tracklet_id"]), int(row["frame"])): row
        for row in global_observation_ownership(documents["global_identity.json"])
    }
    canonical_integrity = artifact_membership_integrity(
        documents["global_identity.json"], documents["stable_players.json"]
    )
    candidate_by_tracklet, candidate_player_by_id = _candidate_membership(
        documents["identity_candidate_shadow.json"]
    )
    reviewed_assignments = {
        str(row.get("tracklet_id")): row
        for row in documents["reviewed_identity_snapshot.json"].get("tracklet_assignments") or []
        if row.get("tracklet_id")
    }
    overrides = observation_index(
        list(documents["reviewed_identity_snapshot.json"].get("observation_overrides") or [])
    )
    demotions = observation_index(
        list(documents["reviewed_identity_snapshot.json"].get("observation_demotions") or [])
    )
    canonical_observations = observation_index(
        list(
            documents["reviewed_identity_snapshot.json"].get(
                "canonical_observation_assignments"
            )
            or []
        )
    )
    observations = _observations(
        tracklets,
        global_slots,
        stable_slots,
        candidate_by_tracklet,
        candidate_player_by_id,
        reviewed_assignments,
        overrides,
        demotions,
        canonical_observations,
        canonical_ownership,
        fps,
    )
    _mark_suspected_upstream_fragmentation(observations)
    for row in observations:
        row["comparison_status"] = classify_observation(row)
        row["reviewed_slot_loss_scope"] = _reviewed_slot_loss_scope(row)

    roster = _roster(documents["match.json"])
    report = {
        "schema_version": "3.0.0",
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "match_id": str(documents["match.json"].get("id") or match_path.name),
        "source_artifacts": _artifact_descriptor(match_path, documents, optional_documents, tracks_document),
        "pipeline_versions": {
            "global_identity": documents["global_identity.json"].get("resolver_version")
            or documents["global_identity.json"].get("schema_version"),
            "stable_players": documents["stable_players.json"].get("algorithm_version")
            or documents["stable_players.json"].get("schema_version"),
            "reviewed_identity_snapshot": documents["reviewed_identity_snapshot.json"].get("source", {}).get("algorithm_version")
            or documents["reviewed_identity_snapshot.json"].get("schema_version"),
        },
        "fps": fps,
        "summary": _summary(observations, fps),
        "canonical_artifact_integrity": canonical_integrity,
        "canonical_multi_slot_claims": _canonical_multi_slot_claims(
            documents["global_identity.json"],
            documents["stable_players.json"],
            canonical_integrity,
            observations,
        ),
        "same_tracklet_findings": _same_tracklet_findings(observations),
        "team_unknown_cases": _team_unknown_cases(observations),
        "per_stable_slot_fragmentation": _fragmentation(observations),
        "case_studies": _case_studies(
            observations, roster, optional_documents, case_names
        ),
        "source_precedence": _source_precedence(),
        "frame_level_comparison": observations,
        "conclusion": _conclusion(observations),
        "recommendations": _recommendations(observations),
        "safety": {
            "source_artifacts_read_only": True,
            "source_artifacts_hashes_before": before_hashes,
            "source_artifacts_hashes_after": _artifact_hashes(match_path),
            "source_artifacts_unchanged": before_hashes == _artifact_hashes(match_path),
            "reran_yolo": False,
            "reran_tracking": False,
            "production_identity_mutated": False,
        },
    }
    report["roman_gap_study"] = _roman_gap_study(observations, report["case_studies"])
    return report


def classify_observation(row: dict[str, Any]) -> str:
    """Classify only direct, same-tracklet evidence.

    A candidate subject spanning two stable slots is intentionally not part of
    this decision tree: it is an indication requiring separate evidence, not a
    definitive core-stabilization switch.
    """
    global_slot = _slot(row.get("global_stable_player_id"))
    reviewed_slot = _slot(row.get("reviewed_stable_slot_id"))
    tracklet_team = str(row.get("tracklet_team_label") or "U")
    reviewed_team = str(row.get("reviewed_team_label") or "U")
    global_team = str(row.get("global_team_label") or _slot_team(global_slot))

    if row.get("upstream_multi_slot_tracklet_membership"):
        if (
            global_slot
            and tracklet_team in {"A", "B"}
            and global_team in {"A", "B"}
            and tracklet_team != global_team
        ):
            return "real_canonical_ab_team_conflict"
        return (
            "upstream_multi_slot_tracklet"
            if global_slot
            else "upstream_multi_slot_ownership_gap"
        )
    if not global_slot:
        return "upstream_unknown" if tracklet_team == reviewed_team == "U" else "missing_global_lineage"
    if (
        tracklet_team == "U"
        and global_team in {"A", "B"}
        and reviewed_team == "U"
        and reviewed_slot is None
    ):
        return "definite_reviewed_team_regression"
    if reviewed_slot is None:
        return "definite_reviewed_slot_loss"
    if reviewed_slot != global_slot:
        return "definite_reviewed_slot_regression"
    return "same_tracklet_exact"


def compact_reviewed_identity_regression_report(report: dict[str, Any]) -> dict[str, Any]:
    """Return a commit-safe report without the full observation table."""
    return {key: value for key, value in report.items() if key != "frame_level_comparison"}


def add_before_after_validation(
    report: dict[str, Any], baseline_report: dict[str, Any]
) -> dict[str, Any]:
    """Attach compact baseline comparisons without retaining frame-level rows."""
    baseline_cases = {
        str(row.get("requested_name") or ""): row
        for row in baseline_report.get("case_studies") or []
    }
    after_cases = {
        str(row.get("requested_name") or ""): row
        for row in report.get("case_studies") or []
    }
    names = list(dict.fromkeys([*baseline_cases, *after_cases]))
    report["before_after_validation"] = {
        "before_diagnostic_version": baseline_report.get("diagnostic_version"),
        "after_diagnostic_version": report.get("diagnostic_version"),
        "players": [
            _player_before_after(
                name,
                baseline_cases.get(name, {}),
                after_cases.get(name, {}),
            )
            for name in names
        ],
        "team_unknown": {
            "before": baseline_report.get("team_unknown_cases") or {},
            "after": report.get("team_unknown_cases") or {},
        },
        "slot_losses": {
            "before": (baseline_report.get("summary") or {}).get(
                "reviewed_slot_loss_breakdown"
            )
            or {
                "note": "The v2 baseline did not separate resolver losses, frame-uniqueness demotions, and operator removals."
            },
            "after": (report.get("summary") or {}).get(
                "reviewed_slot_loss_breakdown"
            )
            or {},
            "after_reasons": (report.get("summary") or {}).get(
                "reviewed_slot_loss_reasons"
            )
            or {},
        },
    }
    return report


def render_markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    conclusion = report["conclusion"]
    lines = [
        f"# Reviewed identity regression validation: `{report['match_id']}`",
        "",
        f"**Verdict:** {conclusion['verdict']}",
        "",
        "## Evidence matrix",
        "",
        f"- Direct same-tracklet slot regressions: {summary['same_tracklet']['definite_reviewed_slot_regression']['events']} events / {summary['same_tracklet']['definite_reviewed_slot_regression']['tracklets']} tracklets / {summary['same_tracklet']['definite_reviewed_slot_regression']['observations']} observations.",
        f"- Direct same-tracklet slot losses: {summary['same_tracklet']['definite_reviewed_slot_loss']['events']} events / {summary['same_tracklet']['definite_reviewed_slot_loss']['tracklets']} tracklets / {summary['same_tracklet']['definite_reviewed_slot_loss']['observations']} observations.",
        f"- Resolver slot losses: {summary['reviewed_slot_loss_breakdown']['resolver_slot_loss']['observations']} observations; frame-uniqueness demotions: {summary['reviewed_slot_loss_breakdown']['frame_uniqueness_demotion']['observations']}; operator slot removals: {summary['reviewed_slot_loss_breakdown']['operator_slot_removal']['observations']}.",
        f"- Direct Team-U regressions: {summary['same_tracklet']['definite_reviewed_team_regression']['events']} events / {summary['same_tracklet']['definite_reviewed_team_regression']['tracklets']} tracklets / {summary['same_tracklet']['definite_reviewed_team_regression']['observations']} observations.",
        f"- Suspected upstream fragmentation indications: {summary['suspected_upstream_fragmentation']['candidate_subjects']} candidate subjects (not counted as definitive core switches).",
        "",
        "## Operator-binding case studies",
        "",
    ]
    for case in report["case_studies"]:
        if not case["available"]:
            lines.append(f"- **{case['requested_name']}**: no matching operator decision.")
            continue
        lines.append(
            f"- **{case['requested_name']}**: anchor {case['anchor_global_stable_slot'] or 'unproven'}; "
            f"named coverage {case['named_coverage_ratio']:.1%}; {case['classification']} ({case['evidence_severity']})."
        )
    roman_gap = report.get("roman_gap_study") or {}
    if roman_gap.get("available"):
        lines.extend([
            "",
            "## Roman re-anchor gap",
            "",
            f"- Needs visual/operator confirmation: `{roman_gap.get('needs_visual_operator_confirmation', True)}`.",
            f"- {roman_gap.get('reason')}",
        ])
    canonical_claims = [
        row
        for row in report.get("canonical_multi_slot_claims") or []
        if row.get("source") == "global_identity"
    ]
    if canonical_claims:
        lines.extend(["", "## Frame-level canonical ownership", ""])
        integrity = report.get("canonical_artifact_integrity") or {}
        lines.append(
            f"- Global/stable derived artifact integrity: `{integrity.get('classification')}`."
        )
        for claim in canonical_claims:
            rendered = ", ".join(
                f"{item['label']} @ {item['frame_ranges']}"
                for item in claim.get("reviewed_labels_by_detected_range") or []
            ) or "no reviewed detected observation"
            lines.append(
                f"- `{claim['tracklet_id']}` → `{claim['slot_id']}` at "
                f"{claim.get('detected_frame_ranges') or 'no detected range'}; rendered {rendered}."
            )
    comparison = report.get("before_after_validation")
    if comparison:
        lines.extend(["", "## BEFORE -> AFTER validation", ""])
        for player in comparison["players"]:
            before_coverage = player["before_named_coverage_ratio"]
            after_coverage = player["after_named_coverage_ratio"]
            lines.append(
                f"- **{player['requested_name']}**: anchor {player['anchor_stable_slot'] or 'unproven'}; "
                f"named coverage {_ratio(before_coverage)} -> {_ratio(after_coverage)}; "
                f"first true unnamed frame {player['after_first_frame_without_named_identity'] or 'none'}; "
                f"parallel unnamed fragment {player['after_first_parallel_unnamed_fragment'] or 'none'}; "
                f"remaining {player['after_remaining_reason']}."
            )
        team_unknown = comparison["team_unknown"]
        before_u = (
            team_unknown["before"]
            .get("definite_reviewed_team_u_regressions", {})
            .get("observations", 0)
        )
        after_u = (
            team_unknown["after"]
            .get("definite_reviewed_team_u_regressions", {})
            .get("observations", 0)
        )
        lines.append(
            f"- Team-U direct reviewed regressions: {before_u} -> {after_u} observations."
        )
        losses = comparison["slot_losses"]["after"]
        lines.append(
            "- AFTER slot losses: "
            f"resolver {losses.get('resolver_slot_loss', {}).get('observations', 0)}; "
            f"frame uniqueness {losses.get('frame_uniqueness_demotion', {}).get('observations', 0)}; "
            f"operator removals {losses.get('operator_slot_removal', {}).get('observations', 0)}."
        )
    lines.extend(["", "## Source safety", ""])
    lines.append(f"- Source artifacts unchanged: `{report['safety']['source_artifacts_unchanged']}`.")
    lines.extend(["", "## Recommendations", ""])
    lines.extend(f"- {item}" for item in report["recommendations"])
    return "\n".join(lines) + "\n"


def _player_before_after(
    name: str, before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    return {
        "requested_name": name,
        "anchor_stable_slot": after.get("anchor_global_stable_slot")
        or before.get("anchor_global_stable_slot"),
        "before_named_coverage_ratio": before.get("named_coverage_ratio"),
        "after_named_coverage_ratio": after.get("named_coverage_ratio"),
        "before_first_frame_without_named_identity": before.get(
            "first_frame_without_named_identity"
        ),
        "after_first_frame_without_named_identity": after.get(
            "first_frame_without_named_identity"
        ),
        "before_first_parallel_unnamed_fragment": before.get(
            "first_parallel_unnamed_fragment"
        ),
        "after_first_parallel_unnamed_fragment": after.get(
            "first_parallel_unnamed_fragment"
        ),
        "before_remaining_reason": before.get("classification"),
        "after_remaining_reason": after.get("remaining_review_reasons")
        or after.get("classification"),
    }


def _ratio(value: Any) -> str:
    return f"{float(value):.1%}" if isinstance(value, (int, float)) else "n/a"


def _observations(
    tracklets: dict[str, dict[str, Any]],
    global_slots: dict[str, list[dict[str, Any]]],
    stable_slots: dict[str, list[dict[str, Any]]],
    candidate_by_tracklet: dict[str, list[str]],
    candidate_player_by_id: dict[str, str | None],
    reviewed_assignments: dict[str, dict[str, Any]],
    overrides: dict[tuple[str, int], dict[str, Any]],
    demotions: dict[tuple[str, int], dict[str, Any]],
    canonical_observations: dict[tuple[str, int], dict[str, Any]],
    canonical_ownership: dict[tuple[str, int], dict[str, Any]],
    fps: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tracklet_id, tracklet in sorted(tracklets.items()):
        assignment = reviewed_assignments.get(tracklet_id, {})
        global_claims = global_slots.get(tracklet_id, [])
        stable_claims = stable_slots.get(tracklet_id, [])
        global_labels = {
            str(claim["stable_slot_id"]) for claim in global_claims
        }
        stable_labels = {
            str(claim["stable_slot_id"]) for claim in stable_claims
        }
        candidate_ids = candidate_by_tracklet.get(tracklet_id, [])
        for position in tracklet.get("positions_m") or []:
            if not is_real_detected_position(position):
                continue
            frame = int(position.get("frame") or 0)
            global_claim = (
                canonical_ownership.get((tracklet_id, frame))
                if len(global_labels) > 1
                else global_claims[0]
                if global_claims
                else {}
            ) or {}
            stable_claim = (
                stable_claims[0]
                if len(stable_labels) == 1 and stable_claims
                else {}
            )
            effective = (
                effective_reviewed_observation(
                    assignment,
                    position,
                    overrides,
                    demotions,
                    canonical_observations,
                )
                if assignment
                else position
            )
            rows.append(
                {
                    "frame": frame,
                    "time_sec": round(frame / fps, 3),
                    "tracklet_id": tracklet_id,
                    "tracklet_team_label": str(tracklet.get("team_label") or "U"),
                    "global_stable_subject_id": global_claim.get("stable_subject_id"),
                    "global_stable_player_id": global_claim.get("stable_slot_id"),
                    "global_team_label": global_claim.get("team_label") or "U",
                    "stable_player_id": stable_claim.get("stable_slot_id"),
                    "stable_team_label": stable_claim.get("team_label") or "U",
                    "candidate_subject_id": candidate_ids[0] if len(candidate_ids) == 1 else None,
                    "candidate_subject_ids": candidate_ids,
                    "candidate_player_id": candidate_player_by_id.get(candidate_ids[0]) if len(candidate_ids) == 1 else None,
                    "reviewed_stable_slot_id": effective.get("stable_anonymous_slot_id"),
                    "reviewed_identity_status": effective.get("identity_status") or "missing",
                    "reviewed_identity_source": effective.get("identity_source"),
                    "reviewed_canonical_player_id": effective.get("canonical_player_id"),
                    "reviewed_display_label": effective.get("display_label") or effective.get("fallback_label"),
                    "reviewed_team_label": effective.get("team_label") or "U",
                    "reviewed_hard_blockers": list(effective.get("hard_blockers") or []),
                    "reviewed_conflict_codes": sorted(
                        {
                            str(conflict.get("code") or "")
                            for conflict in effective.get("conflicts") or []
                            if conflict.get("code")
                        }
                    ),
                    "reviewed_observation_demoted": (tracklet_id, frame) in demotions,
                    "upstream_multi_slot_tracklet_membership": len(global_labels) > 1,
                    "global_slot_claims": global_claims,
                    "stable_slot_claims": stable_claims,
                    "suspected_upstream_fragmentation": False,
                }
            )
    return sorted(rows, key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))


def _mark_suspected_upstream_fragmentation(observations: list[dict[str, Any]]) -> None:
    """Mark downstream grouping that spans slots as suspicion, never as proof."""
    slots_by_candidate: dict[str, set[str]] = defaultdict(set)
    for row in observations:
        candidate = row.get("candidate_subject_id")
        slot = _slot(row.get("global_stable_player_id"))
        if candidate and slot:
            slots_by_candidate[str(candidate)].add(slot)
    for row in observations:
        candidate = row.get("candidate_subject_id")
        row["suspected_upstream_fragmentation"] = bool(
            candidate and len(slots_by_candidate[str(candidate)]) > 1
        )


def _metric(rows: list[dict[str, Any]], fps: float) -> dict[str, Any]:
    tracklets = {str(row["tracklet_id"]) for row in rows}
    events = _segments(rows, lambda row: (row["tracklet_id"], row["comparison_status"]))
    return {
        "events": len(events),
        "tracklets": len(tracklets),
        "observations": len(rows),
        "duration_sec": round(
            sum((int(event[-1]["frame"]) - int(event[0]["frame"]) + 1) / fps for event in events),
            3,
        ),
    }


def _summary(observations: list[dict[str, Any]], fps: float) -> dict[str, Any]:
    statuses = (
        "same_tracklet_exact",
        "definite_reviewed_slot_regression",
        "definite_reviewed_slot_loss",
        "definite_reviewed_team_regression",
        "upstream_unknown",
        "missing_global_lineage",
        "upstream_multi_slot_tracklet",
        "upstream_multi_slot_ownership_gap",
        "real_canonical_ab_team_conflict",
    )
    by_status = {
        status: _metric([row for row in observations if row["comparison_status"] == status], fps)
        for status in statuses
    }
    suspected = [row for row in observations if row["suspected_upstream_fragmentation"]]
    slot_loss_scopes = (
        "resolver_slot_loss",
        "frame_uniqueness_demotion",
        "operator_slot_removal",
    )
    return {
        "frames_analyzed": len({int(row["frame"]) for row in observations}),
        "detected_observations_analyzed": len(observations),
        "tracklets_analyzed": len({str(row["tracklet_id"]) for row in observations}),
        "same_tracklet": by_status,
        "reviewed_slot_loss_breakdown": {
            scope: _metric(
                [
                    row
                    for row in observations
                    if row.get("reviewed_slot_loss_scope") == scope
                ],
                fps,
            )
            for scope in slot_loss_scopes
        },
        "reviewed_slot_loss_reasons": {
            scope: _loss_reasons(
                [
                    row
                    for row in observations
                    if row.get("reviewed_slot_loss_scope") == scope
                ]
            )
            for scope in slot_loss_scopes
        },
        "suspected_upstream_fragmentation": {
            "candidate_subjects": len({str(row["candidate_subject_id"]) for row in suspected}),
            "tracklets": len({str(row["tracklet_id"]) for row in suspected}),
            "observations": len(suspected),
            "severity": "SUSPECTED",
            "note": "Candidate-subject membership across global slots is not proof of a core switch.",
        },
    }


def _loss_reasons(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _count_rows(
        Counter(
            str(blocker)
            for row in rows
            for blocker in _reviewed_reasons(row)
        ),
        len(rows),
    ) if rows else []


def _reviewed_reasons(row: dict[str, Any]) -> list[str]:
    return list(
        row.get("reviewed_hard_blockers")
        or row.get("reviewed_conflict_codes")
        or ["no_recorded_blocker"]
    )


def _same_tracklet_findings(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    direct_statuses = {
        "definite_reviewed_slot_regression",
        "definite_reviewed_slot_loss",
        "definite_reviewed_team_regression",
    }
    for segment in _segments(
        [row for row in observations if row["comparison_status"] in direct_statuses],
        lambda row: (row["tracklet_id"], row["comparison_status"], row.get("reviewed_stable_slot_id")),
    ):
        first = segment[0]
        findings.append(
            {
                "severity": "DEFINITE",
                "classification": first["comparison_status"],
                "frame_start": first["frame"],
                "frame_end": segment[-1]["frame"],
                "time_start_sec": first["time_sec"],
                "time_end_sec": segment[-1]["time_sec"],
                "tracklet_id": first["tracklet_id"],
                "global_slot": first["global_stable_player_id"],
                "global_team": first["global_team_label"],
                "tracklet_team": first["tracklet_team_label"],
                "reviewed_slot": first["reviewed_stable_slot_id"],
                "reviewed_team": first["reviewed_team_label"],
                "reviewed_label": first["reviewed_display_label"],
                "hard_blockers": first["reviewed_hard_blockers"],
                "reason": _finding_reason(first),
            }
        )
    return findings


def _finding_reason(row: dict[str, Any]) -> str:
    if row["comparison_status"] == "definite_reviewed_team_regression":
        return "Global A/B anchor exists, local tracklet team is U, and reviewed output remains U without a stable slot."
    if row["comparison_status"] == "definite_reviewed_slot_loss":
        return "The same tracklet has a global stable slot but reviewed output has no stable slot."
    return "The same tracklet has different global and reviewed stable slots."


def _team_unknown_cases(observations: list[dict[str, Any]]) -> dict[str, Any]:
    local_unknown = [row for row in observations if str(row["tracklet_team_label"]) == "U"]
    anchored = [row for row in local_unknown if _slot(row.get("global_stable_player_id"))]
    preserved = [
        row for row in anchored
        if str(row["reviewed_team_label"]) in {"A", "B"}
        and _slot(row.get("reviewed_stable_slot_id"))
    ]
    degraded = [row for row in anchored if row["comparison_status"] == "definite_reviewed_team_regression"]
    upstream_unknown = [row for row in local_unknown if row["comparison_status"] == "upstream_unknown"]
    conflicts = [
        row for row in observations
        if _slot(row.get("global_stable_player_id"))
        and str(row["tracklet_team_label"]) in {"A", "B"}
        and str(row["global_team_label"]) in {"A", "B"}
        and row["tracklet_team_label"] != row["global_team_label"]
    ]
    return {
        "local_team_u": _case_metric(local_unknown),
        "global_ab_anchor_with_local_team_u": _case_metric(anchored),
        "review_preserved_global_ab_anchor": _case_metric(preserved),
        "definite_reviewed_team_u_regressions": _case_metric(degraded),
        "upstream_unknown": _case_metric(upstream_unknown),
        "potential_local_global_team_conflicts": _case_metric(conflicts),
        "degradation_hard_blockers": _count_rows(
            Counter(
                str(blocker)
                for row in degraded
                for blocker in (row.get("reviewed_hard_blockers") or ["none"])
            ),
            len(degraded),
        ) if degraded else [],
        "remaining_reviewed_u_classes": _count_rows(
            Counter(
                        f"{row['comparison_status']}:{','.join(_reviewed_reasons(row))}"
                for row in anchored
                if row not in preserved
            ),
            len([row for row in anchored if row not in preserved]),
        )
        if any(row not in preserved for row in anchored)
        else [],
        "degradation_reason": "A direct same-tracklet loss of a global A/B anchor after review; local/global A-B conflicts are reported separately and never classified as this regression.",
        "examples": [
            {
                key: row[key]
                for key in (
                    "frame", "time_sec", "tracklet_id", "global_stable_player_id",
                    "global_team_label", "tracklet_team_label", "reviewed_stable_slot_id",
                    "reviewed_team_label", "reviewed_display_label", "reviewed_hard_blockers",
                )
            }
            for row in degraded[:20]
        ],
    }


def _case_metric(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {"observations": len(rows), "tracklets": len({str(row["tracklet_id"]) for row in rows})}


def _fragmentation(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        slot = _slot(row.get("global_stable_player_id"))
        if slot:
            grouped[slot].append(row)
    output = []
    for slot, rows in grouped.items():
        labels = Counter(str(row.get("reviewed_display_label") or "missing") for row in rows)
        canonical = Counter(str(row.get("reviewed_canonical_player_id") or "none") for row in rows)
        teams = Counter(str(row.get("reviewed_team_label") or "U") for row in rows)
        transitions = _label_transitions(rows)
        output.append(
            {
                "global_stable_slot_id": slot,
                "tracklets": len({str(row["tracklet_id"]) for row in rows}),
                "candidate_subjects": sorted({str(row["candidate_subject_id"]) for row in rows if row.get("candidate_subject_id")}),
                "reviewed_entities": sorted({str(row.get("reviewed_stable_slot_id") or row.get("reviewed_canonical_player_id") or "none") for row in rows}),
                "reviewed_labels": _count_rows(labels, len(rows)),
                "reviewed_canonical_players": _count_rows(canonical, len(rows)),
                "reviewed_teams": _count_rows(teams, len(rows)),
                "dominant_reviewed_label": labels.most_common(1)[0][0] if labels else None,
                "dominant_label_ratio": round(labels.most_common(1)[0][1] / len(rows), 4) if rows else 0.0,
                **transitions,
            }
        )
    return sorted(
        output,
        key=lambda row: (
            -int(row["named_to_fallback_transitions"] + row["fallback_to_named_transitions"] + row["ab_to_u_transitions"]),
            float(row["dominant_label_ratio"]),
            str(row["global_stable_slot_id"]),
        ),
    )


def _count_rows(counter: Counter[str], total: int) -> list[dict[str, Any]]:
    return [{"value": value, "observations": count, "ratio": round(count / total, 4)} for value, count in counter.most_common()]


def _label_transitions(rows: list[dict[str, Any]]) -> dict[str, int]:
    previous: dict[str, dict[str, Any]] = {}
    counts = Counter()
    for row in sorted(rows, key=lambda item: (int(item["frame"]), str(item["tracklet_id"]))):
        key = str(row["tracklet_id"])
        prior = previous.get(key)
        if prior:
            if prior.get("reviewed_display_label") != row.get("reviewed_display_label"):
                counts["reviewed_label_transitions"] += 1
            if _is_named(prior) and _is_fallback(row):
                counts["named_to_fallback_transitions"] += 1
            if _is_fallback(prior) and _is_named(row):
                counts["fallback_to_named_transitions"] += 1
            if prior.get("reviewed_team_label") in {"A", "B"} and row.get("reviewed_team_label") == "U":
                counts["ab_to_u_transitions"] += 1
        previous[key] = row
    return {
        "reviewed_label_transitions": counts["reviewed_label_transitions"],
        "named_to_fallback_transitions": counts["named_to_fallback_transitions"],
        "fallback_to_named_transitions": counts["fallback_to_named_transitions"],
        "ab_to_u_transitions": counts["ab_to_u_transitions"],
    }


def _case_studies(
    observations: list[dict[str, Any]],
    roster: dict[str, str],
    optional_documents: dict[str, dict[str, Any]],
    names: tuple[str, ...],
) -> list[dict[str, Any]]:
    decisions = _operator_decisions(optional_documents)
    normalized_roster = {player_id: _normalized_name(name) for player_id, name in roster.items()}
    output = []
    for requested_name in names:
        target = _normalized_name(requested_name)
        player_ids = [player_id for player_id, name in normalized_roster.items() if target in name or name in target]
        matching = [row for row in decisions if str(row.get("player_id") or "") in player_ids]
        candidate_ids = sorted({str(row["candidate_subject_id"]) for row in matching if row.get("candidate_subject_id")})
        candidate_rows = [row for row in observations if row.get("candidate_subject_id") in candidate_ids]
        slots_seen = sorted({_slot(row.get("global_stable_player_id")) for row in candidate_rows if _slot(row.get("global_stable_player_id"))})
        anchor = slots_seen[0] if len(slots_seen) == 1 else None
        slot_rows = [row for row in observations if anchor and _slot(row.get("global_stable_player_id")) == anchor]
        scope = slot_rows if anchor else candidate_rows
        named_rows = [row for row in scope if str(row.get("reviewed_canonical_player_id") or "") in player_ids]
        fallback_rows = [row for row in scope if _is_fallback(row)]
        unresolved_rows = [row for row in scope if not _is_named(row) and not _is_fallback(row)]
        other_name_rows = [row for row in scope if _is_named(row) and row not in named_rows]
        remaining_rows = [row for row in scope if row not in named_rows]
        first_loss = _first_loss_after_name(scope, player_ids)
        first_unnamed = _first_unnamed_identity(scope, player_ids)
        first_parallel_unnamed = _first_parallel_unnamed_fragment(
            scope,
            player_ids,
        )
        fragmented = bool(anchor and named_rows and (fallback_rows or unresolved_rows or other_name_rows))
        classification = "roster_binding_fragmentation" if fragmented else "operator_binding_not_proven" if not anchor else "operator_binding_complete"
        output.append(
            {
                "requested_name": requested_name,
                "available": bool(matching),
                "matched_roster_player_ids": player_ids,
                "operator_decisions": matching,
                "operator_candidate_subject_ids": candidate_ids,
                "anchor_global_stable_slot": anchor,
                "global_slots_seen": slots_seen,
                "reviewed_labels_seen": sorted({str(row.get("reviewed_display_label") or "missing") for row in scope}),
                "observations": len(scope),
                "named_observations": len(named_rows),
                "fallback_observations": len(fallback_rows),
                "unresolved_observations": len(unresolved_rows),
                "other_name_observations": len(other_name_rows),
                "remaining_review_reasons": _count_rows(
                    Counter(
                        f"{row['reviewed_identity_status']}:{','.join(_reviewed_reasons(row))}"
                        for row in remaining_rows
                    ),
                    len(remaining_rows),
                )
                if remaining_rows
                else [],
                "named_coverage_ratio": round(len(named_rows) / len(scope), 4) if scope else 0.0,
                "first_loss_of_name": first_loss,
                "first_frame_without_named_identity": first_unnamed,
                "first_parallel_unnamed_fragment": first_parallel_unnamed,
                "classification": classification,
                "evidence_severity": "DEFINITE" if fragmented else "INSUFFICIENT_EVIDENCE" if not anchor else "STRONG_INDICATION",
                "timeline": _timeline(scope),
            }
        )
    return output


def _roman_gap_study(
    observations: list[dict[str, Any]], case_studies: list[dict[str, Any]]
) -> dict[str, Any]:
    """Describe the reappearance gap without claiming that fragments are one person."""
    roman = next((row for row in case_studies if row["requested_name"] == "Roman"), None)
    if not roman or not roman["available"] or not roman["anchor_global_stable_slot"]:
        return {"available": False, "reason": "No operator-confirmed Roman anchor is available."}
    slot = str(roman["anchor_global_stable_slot"])
    candidates = set(roman["operator_candidate_subject_ids"])
    anchored_rows = [
        row for row in observations
        if row.get("candidate_subject_id") in candidates
        and _slot(row.get("global_stable_player_id")) == slot
    ]
    if not anchored_rows:
        return {"available": False, "reason": "The operator decision has no global-slot lineage."}
    previous = max(anchored_rows, key=lambda row: int(row["frame"]))
    later = [
        row for row in observations
        if _slot(row.get("global_stable_player_id")) == slot
        and int(row["frame"]) > int(previous["frame"])
        and row.get("candidate_subject_id") not in candidates
    ]
    if not later:
        return {
            "available": True,
            "previous": _gap_endpoint(previous),
            "next_fragment": None,
            "needs_visual_operator_confirmation": True,
            "reason": "No later different candidate fragment with the same global slot exists in frozen artifacts.",
        }
    next_row = min(later, key=lambda row: int(row["frame"]))
    return {
        "available": True,
        "previous": _gap_endpoint(previous),
        "next_fragment": _gap_endpoint(next_row),
        "gap_sec": round(float(next_row["time_sec"]) - float(previous["time_sec"]), 3),
        "same_global_slot": True,
        "appearance_evidence": "not rerun; inspect existing optional artifacts separately",
        "stitching_candidate_evidence": "not asserted by this diagnostic",
        "reid_advisory_evidence": "not rerun; not treated as proof",
        "needs_visual_operator_confirmation": True,
        "severity": "INSUFFICIENT_EVIDENCE",
        "reason": "A common global slot is a stability observation, not proof that the two fragments are the same real-world player.",
    }


def _gap_endpoint(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "frame": row["frame"],
        "time_sec": row["time_sec"],
        "tracklet_id": row["tracklet_id"],
        "candidate_subject_id": row.get("candidate_subject_id"),
        "global_slot": row.get("global_stable_player_id"),
        "global_team": row.get("global_team_label"),
        "tracklet_team": row.get("tracklet_team_label"),
    }


def _operator_decisions(documents: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for filename, document in documents.items():
        for row in document.get("decisions") or []:
            if row.get("candidate_subject_id") and row.get("player_id"):
                output.append({"source_artifact": filename, **row})
    return output


def _first_loss_after_name(rows: list[dict[str, Any]], player_ids: list[str]) -> dict[str, Any] | None:
    named_seen = False
    for row in sorted(rows, key=lambda item: (int(item["frame"]), str(item["tracklet_id"]))):
        named = str(row.get("reviewed_canonical_player_id") or "") in player_ids
        if named:
            named_seen = True
        elif named_seen:
            return {"frame": row["frame"], "time_sec": row["time_sec"], "tracklet_id": row["tracklet_id"], "reviewed_label": row["reviewed_display_label"]}
    return None


def _first_unnamed_identity(
    rows: list[dict[str, Any]], player_ids: list[str]
) -> dict[str, Any] | None:
    named_seen = False
    for frame_rows in _rows_by_frame(rows):
        named_rows = [
            row
            for row in frame_rows
            if str(row.get("reviewed_canonical_player_id") or "") in player_ids
        ]
        if named_seen and not named_rows:
            return _identity_gap_endpoint(frame_rows[0])
        if named_rows:
            named_seen = True
    return None


def _first_parallel_unnamed_fragment(
    rows: list[dict[str, Any]],
    player_ids: list[str],
) -> dict[str, Any] | None:
    for frame_rows in _rows_by_frame(rows):
        named_tracklets = {
            str(row["tracklet_id"])
            for row in frame_rows
            if str(row.get("reviewed_canonical_player_id") or "") in player_ids
        }
        unnamed_rows = [
            row
            for row in frame_rows
            if str(row.get("reviewed_canonical_player_id") or "") not in player_ids
            and str(row["tracklet_id"]) not in named_tracklets
        ]
        if named_tracklets and unnamed_rows:
            return _identity_gap_endpoint(unnamed_rows[0])
    return None


def _rows_by_frame(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["frame"])].append(row)
    return [
        sorted(grouped[frame], key=lambda row: str(row["tracklet_id"]))
        for frame in sorted(grouped)
    ]


def _identity_gap_endpoint(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "frame": row["frame"],
        "time_sec": row["time_sec"],
        "tracklet_id": row["tracklet_id"],
        "candidate_subject_id": row.get("candidate_subject_id"),
        "reviewed_label": row.get("reviewed_display_label"),
    }


def _is_named(row: dict[str, Any]) -> bool:
    return bool(row.get("reviewed_canonical_player_id"))


def _is_fallback(row: dict[str, Any]) -> bool:
    slot = _slot(row.get("reviewed_stable_slot_id"))
    return bool(slot and str(row.get("reviewed_display_label") or "") == slot and not row.get("reviewed_canonical_player_id"))


def _timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[list[dict[str, Any]]] = _segments(
        rows,
        lambda row: (
            row["tracklet_id"], row.get("global_stable_player_id"), row.get("reviewed_stable_slot_id"),
            row.get("reviewed_display_label"), row["comparison_status"],
        ),
    )
    return [
        {
            "frame_start": segment[0]["frame"], "frame_end": segment[-1]["frame"],
            "time_start_sec": segment[0]["time_sec"], "time_end_sec": segment[-1]["time_sec"],
            "tracklet_id": segment[0]["tracklet_id"],
            "global_stable_player_id": segment[0].get("global_stable_player_id"),
            "reviewed_stable_slot_id": segment[0].get("reviewed_stable_slot_id"),
            "reviewed_display_label": segment[0].get("reviewed_display_label"),
            "comparison_status": segment[0]["comparison_status"],
        }
        for segment in segments
    ]


def _segments(rows: list[dict[str, Any]], key_fn: Any) -> list[list[dict[str, Any]]]:
    segments: list[list[dict[str, Any]]] = []
    for row in sorted(rows, key=lambda item: (str(item["tracklet_id"]), int(item["frame"]))):
        key = key_fn(row)
        if segments and key_fn(segments[-1][0]) == key and int(row["frame"]) <= int(segments[-1][-1]["frame"]) + 1:
            segments[-1].append(row)
        else:
            segments.append([row])
    return segments


def _source_precedence() -> dict[str, Any]:
    return {
        "stable_anonymous_resolution": [
            "manual reviewed slot decision (when valid)",
            "manual team/referee/false/team-unknown action",
            "global_identity is the canonical stable-slot source; stable_players is a derived integrity-checked view",
            "gallery and candidate claims are advisory and cannot erase a canonical anchor",
            "a canonical A/B slot supplies team identity when the local tracklet team is U",
            "an opposite local A/B team remains a hard compatibility conflict",
        ],
        "reviewed_snapshot": [
            "a safe manual stable-slot roster binding applies to every canonical slot fragment",
            "legacy subject roster decisions are promoted only when they map to exactly one canonical slot",
            "fresh seeded candidate decisions remain subject-scoped",
            "manual team action overrides the resolved team/status",
            "invalid/cross-team/conflicting evidence clears the canonical player",
            "stable anonymous slot remains the fallback display label",
        ],
        "per_observation": [
            "tracklet assignment",
            "global frame-level canonical ownership for multi-slot tracklets",
            "exact observation override",
            "frame-uniqueness safety demotion",
        ],
        "rendering": "reviewed video renders the effective reviewed observation; it does not re-resolve identity.",
        "frame_safety": "a frame-uniqueness demotion is an observation-level safety action, not a resolver slot loss.",
    }


def _conclusion(observations: list[dict[str, Any]]) -> dict[str, Any]:
    direct = [
        row
        for row in observations
        if row["comparison_status"]
        in {"definite_reviewed_slot_regression", "definite_reviewed_team_regression"}
        or (
            row["comparison_status"] == "definite_reviewed_slot_loss"
            and row.get("reviewed_slot_loss_scope")
            in {"resolver_slot_loss", "operator_slot_removal"}
        )
    ]
    suspected = [row for row in observations if row["suspected_upstream_fragmentation"]]
    roster_fragmented = any(
        _slot(row.get("global_stable_player_id"))
        and _is_named(row) is False
        and row.get("reviewed_stable_slot_id")
        for row in observations
    )
    if direct and suspected:
        verdict = "mixed"
    elif direct or roster_fragmented:
        verdict = "reviewed_primary"
    elif suspected:
        verdict = "upstream_primary"
    else:
        verdict = "insufficient_evidence"
    return {
        "verdict": verdict,
        "method": "Evidence matrix; no numeric comparison across observation and event units.",
        "definite_reviewed_findings_present": bool(direct),
        "suspected_upstream_fragmentation_present": bool(suspected),
        "roster_binding_fragmentation_requires_case_study": roster_fragmented,
        "baseline_note": "Global/stable is compared for information retention, not assumed real-world ground truth.",
    }


def _recommendations(observations: list[dict[str, Any]]) -> list[str]:
    conclusion = _conclusion(observations)["verdict"]
    if conclusion in {"reviewed_primary", "mixed"}:
        return [
            "Preserve a valid global stable slot/team through reviewed resolution unless direct contradictory evidence is recorded.",
            "Bind an operator-confirmed roster name to the verified stable slot only after showing any unresolved cross-fragment evidence.",
        ]
    return [
        "Collect visual/operator confirmation for suspected cross-slot candidate fragments before changing core stitching.",
        "Keep the reviewed layer read-only while separating slot stability from real-world roster correctness.",
    ]


def _slot_claims(
    document: dict[str, Any],
    key: str,
    *,
    source: str,
) -> dict[str, list[dict[str, Any]]]:
    """Keep every slot claim; a multi-slot tracklet must never be overwritten."""
    return slot_claims(document, key, source=source)


def _canonical_multi_slot_claims(
    global_document: dict[str, Any],
    stable_document: dict[str, Any],
    integrity: dict[str, Any],
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    multi_tracklets = {
        str(row["tracklet_id"])
        for source in ("global_identity", "stable_players")
        for row in (
            integrity.get("internal_multi_slot_tracklet_membership", {})
            .get(source, [])
        )
    }
    claims = []
    for source, document, key in (
        ("global_identity", global_document, "slots"),
        ("stable_players", stable_document, "players"),
    ):
        for slot in document.get(key) or []:
            slot_id = _slot(
                slot.get("stable_player_id")
                or slot.get("slot_id")
                or slot.get("stable_subject_id")
            )
            if not slot_id:
                continue
            for tracklet_id in sorted(
                multi_tracklets & {str(value) for value in slot.get("tracklet_ids") or []}
            ):
                frame_details = _claim_frame_ranges(slot, tracklet_id)
                claims.append(
                    {
                        "source": source,
                        "slot_id": slot_id,
                        "team": str(slot.get("team_label") or _slot_team(slot_id)),
                        "stable_subject_id": slot.get("stable_subject_id"),
                        "tracklet_id": tracklet_id,
                        "slot_stints": [
                            stint
                            for stint in slot.get("stints") or []
                            if tracklet_id
                            in {str(value) for value in stint.get("tracklet_ids") or []}
                        ],
                        **frame_details,
                        "reviewed_labels_by_detected_range": _reviewed_labels_by_range(
                            observations,
                            tracklet_id,
                            slot_id,
                            frame_details["detected_frame_ranges"],
                        )
                        if source == "global_identity"
                        else [],
                    }
                )
    return sorted(
        claims,
        key=lambda row: (
            str(row["tracklet_id"]),
            str(row["source"]),
            str(row["slot_id"]),
        ),
    )


def _reviewed_labels_by_range(
    observations: list[dict[str, Any]],
    tracklet_id: str,
    slot_id: str,
    frame_ranges: list[list[int]],
) -> list[dict[str, Any]]:
    frames = {
        frame
        for start, end in frame_ranges
        for frame in range(int(start), int(end) + 1)
    }
    by_label: dict[str, list[int]] = defaultdict(list)
    for row in observations:
        if (
            str(row.get("tracklet_id")) == tracklet_id
            and int(row.get("frame") or 0) in frames
            and _slot(row.get("global_stable_player_id")) == slot_id
        ):
            by_label[str(row.get("reviewed_display_label") or "missing")].append(
                int(row["frame"])
            )
    return [
        {"label": label, "frame_ranges": _frame_ranges(values)}
        for label, values in sorted(by_label.items())
    ]


def _claim_frame_ranges(slot: dict[str, Any], tracklet_id: str) -> dict[str, Any]:
    detected_frames: list[int] = []
    overlay_history_frames: list[int] = []
    ranges: dict[str, list[list[int]]] = {}
    for field in ("overlay_positions", "history", "positions_m", "trajectory_m"):
        frames = [
            int(row.get("frame") or 0)
            for row in slot.get(field) or []
            if str(row.get("tracklet_id") or "") == tracklet_id
            and is_real_detected_position(row)
        ]
        ranges[field] = _frame_ranges(frames)
        detected_frames.extend(frames)
        if field in {"overlay_positions", "history"}:
            overlay_history_frames.extend(frames)
    return {
        "detected_frame_ranges": _frame_ranges(detected_frames),
        "overlay_history_frame_ranges": _frame_ranges(overlay_history_frames),
        "first_detected_frame": min(detected_frames) if detected_frames else None,
        "last_detected_frame": max(detected_frames) if detected_frames else None,
        "frame_ranges_by_field": ranges,
    }


def _frame_ranges(frames: list[int]) -> list[list[int]]:
    values = sorted(set(frames))
    if not values:
        return []
    output: list[list[int]] = []
    start = previous = values[0]
    for frame in values[1:]:
        if frame == previous + 1:
            previous = frame
            continue
        output.append([start, previous])
        start = previous = frame
    output.append([start, previous])
    return output


def _candidate_membership(document: dict[str, Any]) -> tuple[dict[str, list[str]], dict[str, str | None]]:
    by_tracklet: dict[str, list[str]] = defaultdict(list)
    player_by_id: dict[str, str | None] = {}
    for row in document.get("subjects") or []:
        subject_id = str(row.get("candidate_subject_id") or "")
        if not subject_id:
            continue
        player_by_id[subject_id] = str(row.get("candidate_player_id")) if row.get("candidate_player_id") else None
        for tracklet_id in row.get("tracklet_ids") or []:
            by_tracklet[str(tracklet_id)].append(subject_id)
    return {key: sorted(value) for key, value in by_tracklet.items()}, player_by_id


def _artifact_descriptor(match_path: Path, documents: dict[str, dict[str, Any]], optional_documents: dict[str, dict[str, Any]], tracks_document: dict[str, Any]) -> list[dict[str, Any]]:
    paths = [*REQUIRED_ARTIFACTS, *OPTIONAL_ARTIFACTS]
    if tracks_document:
        paths.append("tracks.json")
    result = []
    for name in paths:
        path = match_path / name
        document = documents.get(name) or optional_documents.get(name) or tracks_document if name == "tracks.json" else documents.get(name) or optional_documents.get(name)
        result.append({"path": name, "available": path.exists(), "sha256": _sha256(path) if path.exists() else None, "schema_version": document.get("schema_version") if document else None})
    return result


def _artifact_hashes(match_path: Path) -> dict[str, str | None]:
    return {name: _sha256(match_path / name) if (match_path / name).exists() else None for name in (*REQUIRED_ARTIFACTS, *OPTIONAL_ARTIFACTS)}


def _fps(documents: dict[str, dict[str, Any]], tracks_document: dict[str, Any]) -> float:
    for document in (*documents.values(), tracks_document):
        for value in (document.get("fps"), (document.get("metadata") or {}).get("fps"), (document.get("video") or {}).get("fps")):
            try:
                fps = float(value)
            except (TypeError, ValueError):
                continue
            if fps > 0:
                return fps
    return 30.0


def _roster(match: dict[str, Any]) -> dict[str, str]:
    return {str(player.get("id")): str(player.get("name") or "") for team in match.get("teams") or [] for player in team.get("players") or [] if player.get("id")}


def _slot(value: Any) -> str | None:
    match = _STABLE_SLOT.fullmatch(str(value or "").removeprefix("slot-"))
    return f"{match.group('team')}{int(match.group('number')):02d}" if match else None


def _slot_team(slot: str | None) -> str:
    return str(slot or "U")[:1]


def _normalized_name(value: str) -> str:
    translated = value.translate(str.maketrans({"ł": "l", "Ł": "L"}))
    decomposed = unicodedata.normalize("NFKD", translated)
    return "".join(
        character
        for character in decomposed.casefold()
        if not unicodedata.combining(character) and character.isalnum()
    )


def _reviewed_slot_loss_scope(row: dict[str, Any]) -> str | None:
    if row.get("comparison_status") != "definite_reviewed_slot_loss":
        return None
    if row.get("reviewed_observation_demoted"):
        return "frame_uniqueness_demotion"
    if str(row.get("reviewed_identity_source") or "") in {
        "manual_review",
        "operator_team_assignment",
    }:
        return "operator_slot_removal"
    return "resolver_slot_loss"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def _optional(path: Path) -> dict[str, Any]:
    return _load(path) if path.exists() else {}


def _optional_mapping(path: Path) -> dict[str, Any]:
    """Optional legacy artifacts may be JSON arrays; they are not needed here."""
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}
