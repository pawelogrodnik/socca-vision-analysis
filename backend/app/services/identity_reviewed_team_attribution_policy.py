from __future__ import annotations

"""Conservative, explainable team-attribution policy helpers.

The helpers in this module deliberately consume only canonical upstream team
labels.  They never inspect crop pixels and never mutate operator decisions.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic


SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION = "short_track_dominant_team_v1"
SHORT_TRACK_MAX_FRAMES = 200
SHORT_TRACK_MIN_KNOWN_OBSERVATIONS = 15
SHORT_TRACK_MIN_DOMINANT_RATIO = 0.85
# A minority run longer than this is evidence of a possible real merge/switch,
# not harmless isolated classifier noise.  Keep the first version conservative.
SHORT_TRACK_MAX_MINORITY_RUN = 8
TEAM_ATTRIBUTION_POLICY_FILENAME = "reviewed_team_attribution_policy.json"


def team_evidence_features(
    observations: Iterable[Mapping[str, Any]], *, fps: float | None = None
) -> dict[str, Any]:
    """Summarise ordered canonical A/B/U observations for audit and policy."""
    ordered = sorted(
        (
            (int(row.get("frame") or 0), str(row.get("tracklet_id") or ""), _team(row.get("team_label")))
            for row in observations
            if isinstance(row, Mapping)
        ),
        key=lambda item: (item[0], item[1]),
    )
    counts = {team: sum(1 for _frame, _tracklet, label in ordered if label == team) for team in ("A", "B", "U")}
    known = counts["A"] + counts["B"]
    dominant_team = "A" if counts["A"] > counts["B"] else "B" if counts["B"] > counts["A"] else None
    dominant_count = counts[dominant_team] if dominant_team else 0
    known_labels = [label for _frame, _tracklet, label in ordered if label in {"A", "B"}]
    runs = _runs(known_labels)
    longest = {team: max((length for label, length in runs if label == team), default=0) for team in ("A", "B")}
    switches = sum(1 for index in range(1, len(known_labels)) if known_labels[index] != known_labels[index - 1])
    frames = [frame for frame, _tracklet, _label in ordered]
    frame_start = min(frames) if frames else None
    frame_end = max(frames) if frames else None
    frame_count = (frame_end - frame_start + 1) if frame_start is not None else 0
    return {
        "A_observations": counts["A"],
        "B_observations": counts["B"],
        "U_observations": counts["U"],
        "known_team_observations": known,
        "dominant_team": dominant_team,
        "dominant_ratio": round(dominant_count / known, 4) if known else 0.0,
        "team_switch_count": switches,
        "longest_A_run": longest["A"],
        "longest_B_run": longest["B"],
        "frame_start": frame_start,
        "frame_end": frame_end,
        "source_frame_count": frame_count,
        "source_duration_sec": round(frame_count / fps, 3) if fps and frame_count else 0.0,
    }


def short_track_dominant_team_assignment(
    features: Mapping[str, Any], *, structural_conflict: bool = False,
    operator_contradiction: bool = False, stale_source: bool = False,
) -> dict[str, Any] | None:
    """Return a safe derived team assignment, or ``None`` when evidence is weak.

    An opposing run may be short only when it is isolated noise.  We therefore
    reject every long minority run and every alternating source even when its
    simple vote ratio happens to be high.
    """
    dominant = _team(features.get("dominant_team"))
    known = int(features.get("known_team_observations") or 0)
    source_frames = int(features.get("source_frame_count") or 0)
    ratio = float(features.get("dominant_ratio") or 0.0)
    if (
        dominant not in {"A", "B"}
        or source_frames <= 0
        or source_frames > SHORT_TRACK_MAX_FRAMES
        or known < SHORT_TRACK_MIN_KNOWN_OBSERVATIONS
        or ratio < SHORT_TRACK_MIN_DOMINANT_RATIO
        or structural_conflict
        or operator_contradiction
        or stale_source
    ):
        return None
    minority = "B" if dominant == "A" else "A"
    if int(features.get(f"longest_{minority}_run") or 0) > SHORT_TRACK_MAX_MINORITY_RUN:
        return None
    return {
        "team_label": dominant,
        "provenance": SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION,
        "policy_version": SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION,
        "features": dict(features),
    }


def persist_automatic_team_assignments(match_path, units: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Persist a derived, replaceable policy projection without touching decisions."""
    assignments = []
    for unit in units:
        assignment = unit.get("automatic_team_assignment")
        if not isinstance(assignment, Mapping):
            continue
        assignments.append({
            "candidate_subject_id": unit.get("candidate_subject_id"),
            "review_target_id": unit.get("review_target_id"),
            "source_ownership_digest": unit.get("source_ownership_digest"),
            "tracklet_ids": list(unit.get("tracklet_ids") or []),
            "assignment": dict(assignment),
        })
    document = {
        "schema_version": "1.0.0",
        "policy_version": SHORT_TRACK_DOMINANT_TEAM_POLICY_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assignments": assignments,
    }
    write_identity_json_atomic(match_path / TEAM_ATTRIBUTION_POLICY_FILENAME, document)
    return document


def _runs(labels: list[str]) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    for label in labels:
        if result and result[-1][0] == label:
            result[-1] = (label, result[-1][1] + 1)
        else:
            result.append((label, 1))
    return result


def _team(value: Any) -> str:
    normalized = str(value or "U").upper()
    return normalized if normalized in {"A", "B"} else "U"
