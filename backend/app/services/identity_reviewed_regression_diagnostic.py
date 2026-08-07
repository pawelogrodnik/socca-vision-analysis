from __future__ import annotations

"""Read-only A/B diagnostics for stable and reviewed identity artifacts."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from app.services.identity_reviewed_effective_observation import (
    effective_reviewed_observation,
    is_real_detected_position,
    observation_index,
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
DIAGNOSTIC_VERSION = "reviewed_identity_regression_diagnostic:v1"
DEFAULT_CASE_NAMES = ("Mati GK", "Przemek", "Andrzej", "Roman", "Krzysiek", "Piotrek", "Paweł")
_STABLE_SLOT = re.compile(r"^(?P<team>[AB])(?P<number>\d+)(?:~\d+)?$")


def build_reviewed_identity_regression_diagnostic(
    match_path: Path,
    *,
    case_names: tuple[str, ...] = DEFAULT_CASE_NAMES,
) -> dict[str, Any]:
    """Compare frozen stable and reviewed identity artifacts without writing match data."""
    missing = [name for name in REQUIRED_ARTIFACTS if not (match_path / name).exists()]
    if missing:
        raise FileNotFoundError(
            f"Missing required diagnostic artifacts in {match_path}: {', '.join(missing)}"
        )

    documents = {name: _load(match_path / name) for name in REQUIRED_ARTIFACTS}
    optional_documents = {
        name: _optional(match_path / name) for name in OPTIONAL_ARTIFACTS
    }
    tracks_document = _optional(match_path / "tracks.json")
    fps = _fps(documents, tracks_document)
    match = documents["match.json"]
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in documents["tracklets.json"].get("tracklets") or []
        if row.get("tracklet_id")
    }
    global_slots = _slot_claims(documents["global_identity.json"], "slots")
    stable_slots = _slot_claims(documents["stable_players.json"], "players")
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
    observations = _observations(
        tracklets,
        global_slots,
        stable_slots,
        candidate_by_tracklet,
        candidate_player_by_id,
        reviewed_assignments,
        overrides,
        demotions,
        fps,
    )
    _mark_upstream_switches(observations)
    for row in observations:
        row["comparison_status"] = classify_observation(row)

    roster = _roster(match)
    report = {
        "schema_version": "1.0.0",
        "diagnostic_version": DIAGNOSTIC_VERSION,
        "match_id": str(match.get("id") or match_path.name),
        "source_artifacts": _artifact_descriptor(
            match_path, documents, optional_documents, tracks_document
        ),
        "pipeline_versions": {
            "global_identity": documents["global_identity.json"].get("algorithm_version")
            or documents["global_identity.json"].get("schema_version"),
            "stable_players": documents["stable_players.json"].get("algorithm_version")
            or documents["stable_players.json"].get("schema_version"),
            "reviewed_identity_snapshot": documents["reviewed_identity_snapshot.json"].get("source", {}).get("algorithm_version")
            or documents["reviewed_identity_snapshot.json"].get("schema_version"),
        },
        "fps": fps,
        "summary": _summary(observations),
        "switches": _switches(observations),
        "team_unknown_cases": _team_unknown_cases(observations),
        "per_stable_slot_fragmentation": _fragmentation(observations),
        "case_studies": _case_studies(observations, roster, case_names),
        "frame_level_comparison": observations,
        "conclusion": _conclusion(observations),
        "recommendations": _recommendations(observations),
        "safety": {
            "source_artifacts_read_only": True,
            "reran_yolo": False,
            "reran_tracking": False,
            "production_identity_mutated": False,
        },
    }
    return report


def classify_observation(row: dict[str, Any]) -> str:
    """Classify an observation by the layer that lost a stable identity or team."""
    global_slot = _slot(row.get("global_stable_player_id"))
    stable_slot = _slot(row.get("stable_player_id"))
    reviewed_slot = _slot(row.get("reviewed_stable_slot_id"))
    tracklet_team = str(row.get("tracklet_team_label") or "U")
    reviewed_team = str(row.get("reviewed_team_label") or "U")

    if (
        str(row.get("reviewed_identity_source") or "")
        in {"operator_review", "operator_team_assignment"}
        and (reviewed_slot != global_slot or str(row.get("reviewed_identity_status")) == "conflicted")
    ):
        return "operator_decision_interaction"
    if not global_slot:
        if tracklet_team == reviewed_team == "U":
            return "upstream_team_unknown"
        return "missing_lineage"
    if row.get("upstream_global_switch") and reviewed_slot == global_slot:
        return "core_stabilization_switch"
    if (
        tracklet_team == "U"
        and reviewed_team == "U"
        and reviewed_slot is None
        and _slot_team(global_slot) in {"A", "B"}
    ):
        return "team_only_regression"
    if reviewed_slot is None:
        return "reviewed_identity_regression"
    if reviewed_slot != global_slot:
        return "reviewed_identity_regression"
    if stable_slot and stable_slot != global_slot:
        return "core_stabilization_switch"
    return "same"


def render_markdown_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    conclusion = report["conclusion"]
    lines = [
        f"# Reviewed identity regression diagnostic: `{report['match_id']}`",
        "",
        f"**Verdict:** {conclusion['verdict']}",
        "",
        "## Summary",
        "",
        f"- Observations: {summary['observations_analyzed']}",
        f"- Frames: {summary['frames_analyzed']}",
        f"- Core stable switches: {summary['core_stabilization_switches']}",
        f"- Reviewed identity regressions: {summary['reviewed_identity_regressions']}",
        f"- Team-U regressions: {summary['team_u_regressions']}",
        f"- Upstream team-U observations: {summary['upstream_team_unknown']}",
        "",
        "## Case studies",
        "",
    ]
    for case in report["case_studies"]:
        if not case["available"]:
            lines.append(f"- **{case['requested_name']}**: no matching reviewed roster evidence.")
            continue
        lines.append(
            f"- **{case['requested_name']}**: {case['observations']} observations; "
            f"global slots {', '.join(case['global_slots']) or 'none'}; "
            f"statuses {', '.join(case['comparison_statuses']) or 'none'}."
        )
    lines.extend(["", "## Recommendation", ""])
    lines.extend(f"- {item}" for item in report["recommendations"])
    return "\n".join(lines) + "\n"


def _observations(
    tracklets: dict[str, dict[str, Any]],
    global_slots: dict[str, dict[str, Any]],
    stable_slots: dict[str, dict[str, Any]],
    candidate_by_tracklet: dict[str, list[str]],
    candidate_player_by_id: dict[str, str | None],
    reviewed_assignments: dict[str, dict[str, Any]],
    overrides: dict[tuple[str, int], dict[str, Any]],
    demotions: dict[tuple[str, int], dict[str, Any]],
    fps: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tracklet_id, tracklet in sorted(tracklets.items()):
        assignment = reviewed_assignments.get(tracklet_id, {})
        global_claim = global_slots.get(tracklet_id, {})
        stable_claim = stable_slots.get(tracklet_id, {})
        candidate_ids = candidate_by_tracklet.get(tracklet_id, [])
        for position in tracklet.get("positions_m") or []:
            if not is_real_detected_position(position):
                continue
            frame = int(position.get("frame") or 0)
            effective = (
                effective_reviewed_observation(assignment, position, overrides, demotions)
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
                    "candidate_player_id": (
                        candidate_player_by_id.get(candidate_ids[0])
                        if len(candidate_ids) == 1
                        else None
                    ),
                    "reviewed_stable_slot_id": effective.get("stable_anonymous_slot_id"),
                    "reviewed_identity_status": effective.get("identity_status") or "missing",
                    "reviewed_identity_source": effective.get("identity_source"),
                    "reviewed_canonical_player_id": effective.get("canonical_player_id"),
                    "reviewed_display_label": effective.get("display_label") or effective.get("fallback_label"),
                    "reviewed_team_label": effective.get("team_label") or "U",
                    "reviewed_hard_blockers": list(effective.get("hard_blockers") or []),
                    "reviewed_observation_demoted": (tracklet_id, frame) in demotions,
                    "upstream_global_switch": False,
                }
            )
    return sorted(rows, key=lambda row: (int(row["frame"]), str(row["tracklet_id"])))


def _mark_upstream_switches(observations: list[dict[str, Any]]) -> None:
    by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        candidate = row.get("candidate_subject_id")
        if candidate:
            by_candidate[str(candidate)].append(row)
    for rows in by_candidate.values():
        previous_slot: str | None = None
        previous_tracklet: str | None = None
        for row in sorted(rows, key=lambda item: (int(item["frame"]), str(item["tracklet_id"]))):
            slot = _slot(row.get("global_stable_player_id"))
            if (
                slot
                and previous_slot
                and slot != previous_slot
                and str(row["tracklet_id"]) != previous_tracklet
            ):
                row["upstream_global_switch"] = True
            if slot:
                previous_slot = slot
                previous_tracklet = str(row["tracklet_id"])


def _summary(observations: list[dict[str, Any]]) -> dict[str, int]:
    statuses = Counter(str(row["comparison_status"]) for row in observations)
    core_events = {
        (row.get("candidate_subject_id"), row["tracklet_id"], row["global_stable_player_id"])
        for row in observations
        if row["comparison_status"] == "core_stabilization_switch"
    }
    return {
        "observations_analyzed": len(observations),
        "frames_analyzed": len({int(row["frame"]) for row in observations}),
        "tracklets_analyzed": len({str(row["tracklet_id"]) for row in observations}),
        "core_stabilization_switches": len(core_events),
        "reviewed_identity_regressions": statuses["reviewed_identity_regression"],
        "team_u_regressions": statuses["team_only_regression"],
        "upstream_team_unknown": statuses["upstream_team_unknown"],
        "missing_lineage": statuses["missing_lineage"],
        "operator_decision_interaction": statuses["operator_decision_interaction"],
        "same": statuses["same"],
    }


def _switches(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row
        for row in observations
        if row["comparison_status"]
        in {
            "core_stabilization_switch",
            "reviewed_identity_regression",
            "team_only_regression",
            "operator_decision_interaction",
        }
    ]


def _team_unknown_cases(observations: list[dict[str, Any]]) -> dict[str, Any]:
    cases = [
        row
        for row in observations
        if _slot(row.get("global_stable_player_id"))
        and str(row["tracklet_team_label"]) == "U"
    ]
    degradation = [
        row for row in cases if row["comparison_status"] == "team_only_regression"
    ]
    return {
        "stable_slot_with_tracklet_team_u_observations": len(cases),
        "stable_slot_with_tracklet_team_u_tracklets": len(
            {str(row["tracklet_id"]) for row in cases}
        ),
        "degraded_to_reviewed_team_u_observations": len(degradation),
        "examples": cases[:20],
    }


def _fragmentation(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        slot = _slot(row.get("global_stable_player_id"))
        if slot:
            grouped[slot].append(row)
    output = []
    for slot, rows in sorted(grouped.items()):
        labels = Counter(
            str(row.get("reviewed_display_label") or "missing") for row in rows
        )
        entities = {
            str(
                row.get("reviewed_stable_slot_id")
                or row.get("reviewed_canonical_player_id")
                or row.get("reviewed_display_label")
                or "missing"
            )
            for row in rows
        }
        output.append(
            {
                "global_stable_slot_id": slot,
                "global_subjects": len(
                    {
                        str(row.get("global_stable_subject_id"))
                        for row in rows
                        if row.get("global_stable_subject_id")
                    }
                ),
                "tracklets": len({str(row["tracklet_id"]) for row in rows}),
                "candidate_subjects": len(
                    {
                        str(row.get("candidate_subject_id"))
                        for row in rows
                        if row.get("candidate_subject_id")
                    }
                ),
                "reviewed_entities": len(entities),
                "reviewed_labels": [
                    {
                        "label": label,
                        "observations": count,
                        "ratio": round(count / len(rows), 4),
                    }
                    for label, count in labels.most_common()
                ],
            }
        )
    return output


def _case_studies(
    observations: list[dict[str, Any]],
    roster: dict[str, str],
    names: tuple[str, ...],
) -> list[dict[str, Any]]:
    output = []
    normalized_roster = {
        player_id: _normalized_name(name) for player_id, name in roster.items()
    }
    for requested_name in names:
        target = _normalized_name(requested_name)
        player_ids = [
            player_id
            for player_id, name in normalized_roster.items()
            if target in name or name in target
        ]
        rows = [
            row
            for row in observations
            if row.get("reviewed_canonical_player_id") in player_ids
            or _normalized_name(str(row.get("reviewed_display_label") or "")) == target
        ]
        slots = sorted(
            {
                str(row.get("global_stable_player_id"))
                for row in rows
                if row.get("global_stable_player_id")
            }
        )
        if slots:
            rows = [
                row
                for row in observations
                if row.get("candidate_subject_id")
                in {
                    candidate
                    for candidate in (
                        item.get("candidate_subject_id") for item in rows
                    )
                    if candidate
                }
                or row.get("global_stable_player_id") in slots
            ]
        output.append(
            {
                "requested_name": requested_name,
                "available": bool(rows),
                "matched_roster_player_ids": player_ids,
                "global_slots": slots,
                "observations": len(rows),
                "comparison_statuses": sorted(
                    {str(row["comparison_status"]) for row in rows}
                ),
                "timeline": _timeline(rows),
            }
        )
    return output


def _timeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: (int(item["frame"]), str(item["tracklet_id"]))):
        key = (
            row["tracklet_id"],
            row.get("global_stable_player_id"),
            row.get("reviewed_stable_slot_id"),
            row.get("reviewed_display_label"),
            row["comparison_status"],
        )
        if segments and segments[-1]["_key"] == key and int(row["frame"]) <= segments[-1]["frame_end"] + 1:
            segments[-1]["frame_end"] = int(row["frame"])
            segments[-1]["time_end_sec"] = row["time_sec"]
            continue
        segments.append(
            {
                "_key": key,
                "frame_start": int(row["frame"]),
                "frame_end": int(row["frame"]),
                "time_start_sec": row["time_sec"],
                "time_end_sec": row["time_sec"],
                "tracklet_id": row["tracklet_id"],
                "global_stable_player_id": row.get("global_stable_player_id"),
                "reviewed_stable_slot_id": row.get("reviewed_stable_slot_id"),
                "reviewed_display_label": row.get("reviewed_display_label"),
                "comparison_status": row["comparison_status"],
            }
        )
    for segment in segments:
        segment.pop("_key")
    return segments


def _conclusion(observations: list[dict[str, Any]]) -> dict[str, Any]:
    summary = _summary(observations)
    reviewed = summary["reviewed_identity_regressions"] + summary["team_u_regressions"]
    upstream = summary["core_stabilization_switches"] + summary["upstream_team_unknown"]
    if not observations:
        verdict = "insufficient_data"
    elif reviewed > upstream:
        verdict = "reviewed_layer_regression"
    elif upstream > reviewed:
        verdict = "core_stabilization_regression"
    else:
        verdict = "mixed_or_inconclusive"
    return {
        "verdict": verdict,
        "reviewed_layer_observations": reviewed,
        "upstream_observations_or_events": upstream,
        "basis": (
            "Reviewed regressions count stable global slots that are changed, removed, or "
            "degraded to U in reviewed observations. Upstream counts candidate-subject "
            "global-slot transitions and observations already unknown before review."
        ),
    }


def _recommendations(observations: list[dict[str, Any]]) -> list[str]:
    conclusion = _conclusion(observations)["verdict"]
    if conclusion == "reviewed_layer_regression":
        return [
            "Use the global/stable slot as the reviewed canonical backbone and layer operator evidence on top of it.",
            "Preserve a trusted global A/B team anchor when a local tracklet team is U, subject to explicit conflict safeguards.",
        ]
    if conclusion == "core_stabilization_regression":
        return [
            "Improve canonical slot stitching before applying roster enrichment in the reviewed layer.",
            "Keep the reviewed layer read-only over core identity until the stable backbone remains consistent across fragments.",
        ]
    return [
        "Use the frame-level report to isolate the largest regression category before changing identity thresholds or propagation.",
        "Prioritize the stable-slot plus tracklet-team-U cases because their source and reviewed lineage are explicit.",
    ]


def _slot_claims(document: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    claims: dict[str, dict[str, Any]] = {}
    for row in document.get(key) or []:
        slot = _slot(
            row.get("stable_player_id")
            or row.get("slot_id")
            or row.get("stable_subject_id")
        )
        if not slot:
            continue
        for tracklet_id in row.get("tracklet_ids") or []:
            claims[str(tracklet_id)] = {
                "stable_slot_id": slot,
                "stable_subject_id": row.get("stable_subject_id"),
                "team_label": str(row.get("team_label") or _slot_team(slot)),
            }
    return claims


def _candidate_membership(
    document: dict[str, Any],
) -> tuple[dict[str, list[str]], dict[str, str | None]]:
    by_tracklet: dict[str, list[str]] = defaultdict(list)
    player_by_id: dict[str, str | None] = {}
    for row in document.get("subjects") or []:
        subject_id = str(row.get("candidate_subject_id") or "")
        if not subject_id:
            continue
        player_by_id[subject_id] = (
            str(row.get("candidate_player_id")) if row.get("candidate_player_id") else None
        )
        for tracklet_id in row.get("tracklet_ids") or []:
            by_tracklet[str(tracklet_id)].append(subject_id)
    return (
        {tracklet_id: sorted(values) for tracklet_id, values in by_tracklet.items()},
        player_by_id,
    )


def _artifact_descriptor(
    match_path: Path,
    documents: dict[str, dict[str, Any]],
    optional_documents: dict[str, dict[str, Any]],
    tracks_document: dict[str, Any],
) -> list[dict[str, Any]]:
    paths = [*REQUIRED_ARTIFACTS, *OPTIONAL_ARTIFACTS]
    if tracks_document:
        paths.append("tracks.json")
    output = []
    for name in paths:
        path = match_path / name
        document = (
            documents[name]
            if name in documents
            else optional_documents[name]
            if name in optional_documents
            else tracks_document
        )
        output.append(
            {
                "path": name,
                "available": path.exists(),
                "sha256": _sha256(path) if path.exists() else None,
                "schema_version": document.get("schema_version") if document else None,
            }
        )
    return output


def _fps(documents: dict[str, dict[str, Any]], tracks_document: dict[str, Any]) -> float:
    for document in (*documents.values(), tracks_document):
        for value in (
            document.get("fps"),
            (document.get("metadata") or {}).get("fps"),
            (document.get("video") or {}).get("fps"),
        ):
            try:
                fps = float(value)
            except (TypeError, ValueError):
                continue
            if fps > 0:
                return fps
    return 30.0


def _roster(match: dict[str, Any]) -> dict[str, str]:
    return {
        str(player.get("id")): str(player.get("name") or "")
        for team in match.get("teams") or []
        for player in team.get("players") or []
        if player.get("id")
    }


def _slot(value: Any) -> str | None:
    match = _STABLE_SLOT.fullmatch(str(value or "").removeprefix("slot-"))
    return f"{match.group('team')}{int(match.group('number')):02d}" if match else None


def _slot_team(slot: str | None) -> str:
    return str(slot or "U")[:1]


def _normalized_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


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
