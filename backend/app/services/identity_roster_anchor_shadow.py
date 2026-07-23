from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from app.services.identity_jersey_number_common import canonical_structural_blockers, team_label


SCHEMA_VERSION = "0.3.0"
ALGORITHM_NAME = "identity_roster_anchor_shadow"
ALGORITHM_VERSION = "0.5.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "direct_confirmation_ratio": 0.80,
    "reid_support_weight": 0.15,
    "reid_path_discount": 0.75,
    "max_reid_hops": 2,
    "min_edge_support": 0.35,
    "parallel_conflict_tolerance_frames": 1,
}


def build_identity_roster_anchor_shadow(
    candidate_doc: dict[str, Any],
    assignments_doc: dict[str, Any],
    match_doc: dict[str, Any],
    *,
    reid_fusion_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build advisory stable-subject to roster-player review cards.

    Existing manual assignments are the only source of confirmed real-player
    identity. Same-match ReID may rank an unresolved card, but can never create
    an assignment or make the result eligible for statistics.
    """
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    source_match_key = _source_match_key(match_doc)
    source_video_key = _source_video_key(match_doc)
    roster = _roster_players(match_doc, source_match_key=source_match_key)
    subjects = sorted(
        [row for row in candidate_doc.get("subjects") or [] if row.get("candidate_subject_id")],
        key=lambda row: (
            str(row.get("team_label") or "U"),
            int(row.get("start_frame") or 0),
            str(row.get("candidate_subject_id") or ""),
        ),
    )
    subject_by_id = {
        str(row["candidate_subject_id"]): row
        for row in subjects
    }
    direct = {
        subject_id: _direct_assignment_evidence(subject, assignments_doc)
        for subject_id, subject in subject_by_id.items()
    }
    propagated = _propagated_reid_evidence(
        subject_by_id,
        direct,
        reid_fusion_doc or {},
        parameters=params,
    )
    cards = [
        _card(
            subject,
            roster=roster,
            direct_evidence=direct[str(subject["candidate_subject_id"])],
            propagated_evidence=propagated.get(str(subject["candidate_subject_id"])) or {},
            source_match_key=source_match_key,
            source_video_key=source_video_key,
            parameters=params,
        )
        for subject in subjects
    ]
    _apply_parallel_conflicts(cards, parameters=params)
    summary = _summary(cards, roster)
    source = {
        "candidate_identity": candidate_doc.get("algorithm") or {},
        "manual_assignments_schema": assignments_doc.get("schema_version"),
        "reid_fusion": (reid_fusion_doc or {}).get("algorithm") or None,
        "source_match_key": source_match_key,
        "source_video_key": source_video_key,
    }
    safety = {
        "mutates_candidate_identity": False,
        "mutates_production_identity": False,
        "writes_player_identity_assignments": False,
        "automatically_assigns_roster_players": False,
        "automatic_assignments": 0,
        "eligible_for_player_stats": False,
        "eligible_for_heatmaps": False,
        "reid_is_ranking_only": True,
    }
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "parameters": params,
        },
        "source": source,
        "safety": safety,
        "summary": summary,
        "cards": cards,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "algorithm": artifact["algorithm"],
        "status": "ready_for_operator_review" if summary["cards"] else "no_candidate_subjects",
        "summary": summary,
        "gates": {
            "production_identity_untouched": True,
            "manual_assignment_is_only_confirmation_source": True,
            "reid_never_confirms_roster_identity": True,
            "parallel_roster_conflicts_are_blocked": all(
                card.get("recommended_player_id") is None
                for card in cards
                if "parallel_roster_candidate_conflict" in (card.get("reason_codes") or [])
            ),
        },
        "limitations": [
            "Roster candidates without manual or linked same-match evidence remain unranked.",
            "A suggested player always requires an operator decision.",
            "This artifact is excluded from player statistics and heatmaps.",
        ],
    }
    return {
        "identity_roster_anchor_shadow": artifact,
        "identity_roster_anchor_shadow_report": report,
    }


def _roster_players(match_doc: dict[str, Any], *, source_match_key: str | None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for team_label_value, team in _teams(match_doc):
        for player in team.get("players") or []:
            if not isinstance(player, dict) or not player.get("id"):
                continue
            role = str(player.get("role") or "player")
            number = str(player.get("number") or "")
            rows.append(
                {
                    "player_id": str(player["id"]),
                    "player_name": str(player.get("name") or player["id"]),
                    "player_number": player.get("number"),
                    "player_role": role,
                    "is_goalkeeper": _is_goalkeeper(role, number),
                    "source_match_key": source_match_key,
                    "team_id": str(team.get("id") or team.get("team_id") or "").strip() or None,
                    "team_name": str(team.get("name") or f"Team {team_label_value}"),
                    "team_label": team_label_value,
                }
            )
    return sorted(rows, key=lambda row: (row["team_label"], row["player_name"], row["player_id"]))


def _teams(match_doc: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    result: list[tuple[str, dict[str, Any]]] = []
    for index, team in enumerate(match_doc.get("teams") or []):
        if not isinstance(team, dict):
            continue
        explicit = team_label(team.get("team_label") or team.get("label"))
        result.append((explicit if explicit != "U" else ("A" if index == 0 else "B" if index == 1 else "U"), team))
    return result


def _direct_assignment_evidence(
    subject: dict[str, Any],
    assignments_doc: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    subject_start, subject_end = _subject_range(subject)
    production_subjects = {str(value) for value in subject.get("production_subject_ids") or []}
    by_player: dict[str, dict[str, Any]] = {}
    for assignment in assignments_doc.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue
        if assignment.get("status") != "assigned" or not assignment.get("player_id"):
            continue
        if str(assignment.get("team_label") or "U") != str(subject.get("team_label") or "U"):
            continue
        stable_subject_id = str(assignment.get("stable_subject_id") or "")
        if not stable_subject_id or stable_subject_id not in production_subjects:
            continue
        assignment_start = _optional_int(assignment.get("start_frame"))
        assignment_end = _optional_int(assignment.get("end_frame"))
        if assignment_start is None or assignment_end is None:
            assignment_start, assignment_end = subject_start, subject_end
        overlap = _intersection(subject_start, subject_end, assignment_start, assignment_end)
        if overlap is None:
            continue
        player_id = str(assignment["player_id"])
        row = by_player.setdefault(
            player_id,
            {
                "player_id": player_id,
                "player_name": assignment.get("player_name"),
                "intervals": [],
                "assignment_ids": [],
                "anchor_artifacts": [],
                "anchor_confidences": [],
            },
        )
        row["intervals"].append(overlap)
        if assignment.get("stint_id"):
            row["assignment_ids"].append(str(assignment["stint_id"]))
        row["anchor_artifacts"].extend(str(value) for value in assignment.get("anchor_artifacts") or [])
        if isinstance(assignment.get("anchor_confidence"), (int, float)):
            row["anchor_confidences"].append(float(assignment["anchor_confidence"]))

    duration = max(1, subject_end - subject_start + 1)
    for row in by_player.values():
        merged = _merge_intervals(row.pop("intervals"))
        covered = sum(end - start + 1 for start, end in merged)
        confidences = row.pop("anchor_confidences")
        row.update(
            {
                "covered_frames": covered,
                "coverage_ratio": round(min(1.0, covered / duration), 6),
                "assignment_ids": sorted(set(row["assignment_ids"])),
                "anchor_artifacts": sorted(set(row["anchor_artifacts"])),
                "anchor_confidence": round(max(confidences), 6) if confidences else None,
            }
        )
    return by_player


def _propagated_reid_evidence(
    subject_by_id: dict[str, dict[str, Any]],
    direct: dict[str, dict[str, dict[str, Any]]],
    fusion_doc: dict[str, Any],
    *,
    parameters: dict[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    adjacency: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
    min_support = float(parameters["min_edge_support"])
    for proposal in fusion_doc.get("proposals") or []:
        source = str(proposal.get("source_candidate_subject_id") or "")
        target = str(proposal.get("target_candidate_subject_id") or "")
        if source not in subject_by_id or target not in subject_by_id:
            continue
        if str(subject_by_id[source].get("team_label") or "U") != str(
            subject_by_id[target].get("team_label") or "U"
        ):
            continue
        if not proposal.get("strict_gate_passed") or proposal.get("hard_constraint_reasons"):
            continue
        support = max(0.0, 1.0 - float(proposal.get("fused_cost") or 1.0))
        if support < min_support:
            continue
        key = str(proposal.get("proposal_key") or "")
        adjacency[source].append((target, support, key))
        adjacency[target].append((source, support, key))

    propagated: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    max_hops = max(1, int(parameters["max_reid_hops"]))
    discount = float(parameters["reid_path_discount"])
    for seed_subject, players in sorted(direct.items()):
        for player_id, evidence in sorted(players.items()):
            seed_strength = float(evidence.get("coverage_ratio") or 0.0)
            frontier = [(seed_subject, 0, seed_strength, [])]
            best_seen = {seed_subject: seed_strength}
            while frontier:
                current, hops, strength, path = frontier.pop(0)
                if hops >= max_hops:
                    continue
                for neighbor, edge_support, proposal_key in sorted(adjacency.get(current) or []):
                    next_strength = strength * edge_support * discount
                    if next_strength <= best_seen.get(neighbor, -1.0):
                        continue
                    best_seen[neighbor] = next_strength
                    next_path = [*path, proposal_key]
                    frontier.append((neighbor, hops + 1, next_strength, next_path))
                    if neighbor == seed_subject:
                        continue
                    existing = propagated[neighbor].get(player_id)
                    if existing is None or next_strength > float(existing["support"]):
                        propagated[neighbor][player_id] = {
                            "player_id": player_id,
                            "support": round(next_strength, 6),
                            "source_subject_id": seed_subject,
                            "hops": hops + 1,
                            "proposal_keys": next_path,
                        }
    return propagated


def _card(
    subject: dict[str, Any],
    *,
    roster: list[dict[str, Any]],
    direct_evidence: dict[str, dict[str, Any]],
    propagated_evidence: dict[str, dict[str, Any]],
    source_match_key: str | None,
    source_video_key: str | None,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    subject_id = str(subject["candidate_subject_id"])
    team_label = str(subject.get("team_label") or "U")
    role = str(subject.get("role") or "field_player")
    candidates: list[dict[str, Any]] = []
    for player in roster:
        if player["team_label"] != team_label:
            continue
        direct = direct_evidence.get(player["player_id"])
        propagated = propagated_evidence.get(player["player_id"])
        direct_score = float((direct or {}).get("coverage_ratio") or 0.0)
        reid_score = float((propagated or {}).get("support") or 0.0)
        score = direct_score + float(parameters["reid_support_weight"]) * reid_score
        reason_codes: list[str] = []
        if direct:
            reason_codes.append("manual_assignment_overlap")
        if propagated:
            reason_codes.append("p114_fused_path_support")
        if role == "goalkeeper" and not player["is_goalkeeper"]:
            reason_codes.append("role_mismatch")
            score = max(0.0, score - 0.25)
        candidates.append(
            {
                **player,
                "score": round(score, 6),
                "direct_coverage_ratio": round(direct_score, 6),
                "direct_covered_frames": int((direct or {}).get("covered_frames") or 0),
                "anchor_confidence": (direct or {}).get("anchor_confidence"),
                "anchor_artifacts": list((direct or {}).get("anchor_artifacts") or []),
                "assignment_ids": list((direct or {}).get("assignment_ids") or []),
                "reid_path_support": round(reid_score, 6),
                "reid_source_subject_id": (propagated or {}).get("source_subject_id"),
                "reid_proposal_keys": list((propagated or {}).get("proposal_keys") or []),
                "reason_codes": reason_codes,
            }
        )
    candidates.sort(key=lambda row: (-float(row["score"]), row["player_name"], row["player_id"]))
    direct_ranked = sorted(
        direct_evidence.values(),
        key=lambda row: (-float(row["coverage_ratio"]), str(row["player_id"])),
    )
    status = "unresolved"
    recommended_player_id: str | None = None
    reason_codes: list[str] = []
    if len(direct_ranked) > 1:
        status = "conflict"
        reason_codes.append("multiple_manual_players_overlap_subject")
    elif direct_ranked:
        recommended_player_id = str(direct_ranked[0]["player_id"])
        if (
            float(direct_ranked[0]["coverage_ratio"])
            >= float(parameters["direct_confirmation_ratio"])
            and not _candidate_identity_conflict(subject)
        ):
            status = "confirmed_manual_anchor"
            reason_codes.append("manual_assignment_covers_subject")
        else:
            status = "suggested_review"
            reason_codes.append("partial_manual_assignment_evidence")
    elif candidates and float(candidates[0]["reid_path_support"]) > 0.0:
        status = "suggested_review"
        recommended_player_id = str(candidates[0]["player_id"])
        reason_codes.append("p114_ranking_only_suggestion")
    else:
        reason_codes.append("no_roster_identity_evidence")

    recommended = next(
        (row for row in candidates if row["player_id"] == recommended_player_id),
        None,
    )
    team_ids = {player["team_id"] for player in candidates if player.get("team_id")}
    return {
        "anchor_key": _stable_key(subject_id),
        "candidate_subject_id": subject_id,
        "candidate_player_id": subject.get("candidate_player_id"),
        "team_label": team_label,
        "source_match_key": source_match_key,
        "source_video_key": source_video_key,
        "team_id": next(iter(team_ids)) if len(team_ids) == 1 else None,
        "role": role,
        "start_frame": int(subject.get("start_frame") or 0),
        "end_frame": int(subject.get("end_frame") or 0),
        "detected_frames": int(subject.get("detected_frames") or 0),
        "production_subject_ids": sorted(str(value) for value in subject.get("production_subject_ids") or []),
        "quality_flags": sorted(str(value) for value in subject.get("quality_flags") or []),
        "status": status,
        "recommended_player_id": recommended_player_id,
        "recommended_player_name": (recommended or {}).get("player_name"),
        "recommendation_confidence": round(float((recommended or {}).get("score") or 0.0), 6),
        "reason_codes": reason_codes,
        "requires_operator_review": status != "confirmed_manual_anchor",
        "automatic_assignment": False,
        "eligible_for_player_stats": False,
        "roster_candidates": candidates,
    }


def _apply_parallel_conflicts(cards: list[dict[str, Any]], *, parameters: dict[str, Any]) -> None:
    tolerance = int(parameters["parallel_conflict_tolerance_frames"])
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for card in cards:
        player_id = card.get("recommended_player_id")
        if player_id and card["status"] in {"confirmed_manual_anchor", "suggested_review"}:
            by_player[str(player_id)].append(card)
    for rows in by_player.values():
        rows.sort(key=lambda row: (int(row["start_frame"]), str(row["candidate_subject_id"])))
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                overlap = min(int(left["end_frame"]), int(right["end_frame"])) - max(
                    int(left["start_frame"]), int(right["start_frame"])
                ) + 1
                if overlap <= tolerance:
                    continue
                for card in (left, right):
                    card["status"] = "conflict"
                    card["requires_operator_review"] = True
                    card["recommended_player_id"] = None
                    card["recommended_player_name"] = None
                    card["recommendation_confidence"] = 0.0
                    if "parallel_roster_candidate_conflict" not in card["reason_codes"]:
                        card["reason_codes"].append("parallel_roster_candidate_conflict")


def _summary(cards: list[dict[str, Any]], roster: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = defaultdict(int)
    for card in cards:
        status_counts[str(card["status"])] += 1
    return {
        "cards": len(cards),
        "roster_players": len(roster),
        "status_counts": dict(sorted(status_counts.items())),
        "confirmed_manual_anchors": status_counts.get("confirmed_manual_anchor", 0),
        "suggested_review": status_counts.get("suggested_review", 0),
        "unresolved": status_counts.get("unresolved", 0),
        "conflicts": status_counts.get("conflict", 0),
        "parallel_conflicts": sum(
            "parallel_roster_candidate_conflict" in card.get("reason_codes", [])
            for card in cards
        ),
        "automatic_assignments": 0,
        "eligible_for_player_stats": 0,
    }


def _subject_range(subject: dict[str, Any]) -> tuple[int, int]:
    start = int(subject.get("start_frame") or 0)
    end = int(subject.get("end_frame") if subject.get("end_frame") is not None else start)
    return min(start, end), max(start, end)


def _candidate_identity_conflict(subject: dict[str, Any]) -> bool:
    flags = list(subject.get("quality_flags") or [])
    return bool(canonical_structural_blockers(flags)) or "production_anchor_team_mismatch" in flags


def _intersection(left_start: int, left_end: int, right_start: int, right_end: int) -> tuple[int, int] | None:
    start = max(left_start, right_start)
    end = min(left_end, right_end)
    return (start, end) if end >= start else None


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _is_goalkeeper(role: str, number: str) -> bool:
    value = f"{role} {number}".lower()
    return "goalkeeper" in value or "keeper" in value or " gk" in f" {value}"


def _source_match_key(match_doc: dict[str, Any]) -> str | None:
    for field in ("source_match_key", "match_id", "id"):
        value = str(match_doc.get(field) or "").strip()
        if value:
            return value
    return None


def _source_video_key(match_doc: dict[str, Any]) -> str | None:
    metadata = match_doc.get("metadata")
    sources = (match_doc, metadata) if isinstance(metadata, dict) else (match_doc,)
    for field in ("source_video_key", "video_key", "video_id", "video_filename", "video"):
        for source in sources:
            value = source.get(field)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                key = str(value).strip()
                if key:
                    return key
    return None


def _stable_key(subject_id: str) -> str:
    payload = json.dumps({"candidate_subject_id": subject_id}, sort_keys=True, separators=(",", ":"))
    return f"roster-anchor:v1:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"
