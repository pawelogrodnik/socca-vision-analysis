from __future__ import annotations

from collections import Counter
from typing import Any


def build_observation_overrides(
    seeds_document: dict[str, Any],
    tracklets: dict[str, dict[str, Any]],
    roster: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for decision in seeds_document.get("decisions") or []:
        tracklet_id = str((decision.get("provenance") or {}).get("tracklet_id") or decision.get("tracklet_id") or "")
        if tracklet_id not in tracklets:
            continue
        frame = int(decision.get("frame_number") or decision.get("frame") or 0)
        action = str(decision.get("action") or "skip")
        team = _decision_team(action, decision, tracklets[tracklet_id])
        player_id = str((decision.get("assigned_player") or {}).get("player_id") or "") or None
        player = roster.get(player_id or "")
        status = {
            "assign_roster_player": "confirmed",
            "team_a_unknown": "team_unknown",
            "team_b_unknown": "team_unknown",
            "referee": "referee",
            "false_detection": "false_detection",
            "skip": "ignored",
        }.get(action, "ignored")
        blockers: list[str] = []
        if status == "confirmed" and player is None:
            blockers.append("invalid_roster_player")
            status, player_id = "blocked", None
        elif status == "confirmed" and player and player["team_label"] != team:
            blockers.append("cross_team_confirmed_assignment")
            status, player_id = "conflicted", None
        display = (
            player["name"]
            if player and status == "confirmed"
            else "Sędzia"
            if status == "referee"
            else f"Team {team}"
            if status == "team_unknown"
            else status
        )
        output.append(
            {
                "observation_key": str(decision.get("observation_key") or f"{tracklet_id}:{frame}"),
                "tracklet_id": tracklet_id,
                "frame": frame,
                "action": action,
                "identity_status": status,
                "team_label": team,
                "canonical_player_id": player_id if status == "confirmed" else None,
                "player_name": player["name"] if player and status == "confirmed" else None,
                "roster_number": player.get("number") if player and status == "confirmed" else None,
                "display_label": display,
                "hard_blockers": blockers,
                "identity_source": "operator_seed_exact_observation",
            }
        )
    return sorted(output, key=lambda row: (int(row["frame"]), str(row["tracklet_id"]), str(row["observation_key"])))


def observation_coverage(
    tracklets: dict[str, dict[str, Any]],
    assignments: list[dict[str, Any]],
    overrides: list[dict[str, Any]],
) -> dict[str, Any]:
    assignment_by_tracklet = {str(row["tracklet_id"]): row for row in assignments}
    override_by_key = {(str(row["tracklet_id"]), int(row["frame"])): row for row in overrides}
    counts: Counter[str] = Counter()
    unique: set[tuple[str, int]] = set()
    for tracklet_id, tracklet in tracklets.items():
        assignment = assignment_by_tracklet.get(tracklet_id, {})
        for position in tracklet.get("positions_m") or []:
            if str(position.get("status") or "detected") != "detected":
                continue
            key = (tracklet_id, int(position.get("frame") or 0))
            if key in unique:
                continue
            unique.add(key)
            override = override_by_key.get(key)
            status = str((override or assignment).get("identity_status") or "unresolved")
            counts[status] += 1
    reliable = sum(counts[value] for value in ("confirmed", "unresolved", "conflicted", "blocked", "team_unknown"))
    return {
        "detected_observations_total": len(unique),
        "reliable_player_observations_total": reliable,
        "confirmed_detected_observations": counts["confirmed"],
        "unresolved_detected_observations": counts["unresolved"] + counts["team_unknown"],
        "conflicted_detected_observations": counts["conflicted"] + counts["blocked"],
        "ignored_detected_observations": counts["ignored"] + counts["referee"] + counts["false_detection"],
        "exact_named_observations": sum(row["identity_status"] == "confirmed" for row in overrides),
        "confirmed_detected_observation_ratio": round(counts["confirmed"] / reliable, 4) if reliable else None,
        "unresolved_detected_observation_ratio": round((counts["unresolved"] + counts["team_unknown"]) / reliable, 4) if reliable else None,
        "coverage_unit": "unique_detected_tracklet_frame_observation",
    }


def _decision_team(action: str, decision: dict[str, Any], tracklet: dict[str, Any]) -> str:
    if action == "team_a_unknown":
        return "A"
    if action == "team_b_unknown":
        return "B"
    assigned = decision.get("assigned_team") or {}
    return str(assigned.get("team_label") or tracklet.get("team_label") or "U")
