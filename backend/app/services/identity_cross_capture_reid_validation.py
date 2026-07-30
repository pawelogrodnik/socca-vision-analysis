from __future__ import annotations

"""Read-only H1→H2 ground-truth evaluation and operator display gates."""

from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any


DEFAULT_DISPLAY_GATE: dict[str, float | int] = {
    "minimum_internal_queries": 8,
    "minimum_internal_top1": 0.75,
    "minimum_cross_capture_queries": 5,
    "minimum_cross_capture_top1": 0.60,
    "minimum_cross_capture_top3": 0.80,
}


def evaluate_h1_to_h2_cross_capture(
    rankings: list[dict[str, Any]],
    *,
    h2_operator_decisions: list[dict[str, Any]],
    h2_candidate_document: dict[str, Any],
    player_team_by_id: dict[str, str],
) -> dict[str, Any]:
    """Evaluate rankings with H2 decisions, never feeding them into ranking."""

    candidates_by_tracklet: dict[str, list[str]] = defaultdict(list)
    for candidate in h2_candidate_document.get("subjects") or []:
        candidate_id = str(candidate.get("candidate_subject_id") or "")
        for tracklet_id in candidate.get("tracklet_ids") or []:
            if candidate_id:
                candidates_by_tracklet[str(tracklet_id)].append(candidate_id)
    ranking_by_subject = {
        str(row.get("candidate_subject_id") or ""): row
        for row in rankings
        if row.get("candidate_subject_id")
    }
    decisions_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unmapped_decisions: list[dict[str, Any]] = []
    for decision in h2_operator_decisions:
        if decision.get("action") != "assign_roster_player":
            continue
        player = decision.get("assigned_player") or {}
        player_id = str(player.get("player_id") or "")
        team_label = str(
            (decision.get("assigned_team") or {}).get("team_label") or "U"
        )
        tracklet_id = str(
            (decision.get("provenance") or {}).get("tracklet_id") or ""
        )
        candidate_ids = sorted(set(candidates_by_tracklet.get(tracklet_id) or []))
        payload = {
            "observation_key": decision.get("observation_key"),
            "tracklet_id": tracklet_id or None,
            "ground_truth_player_id": player_id,
            "ground_truth_team": team_label,
        }
        if len(candidate_ids) != 1:
            unmapped_decisions.append(
                {
                    **payload,
                    "reason": (
                        "no_candidate_subject_for_exact_observation"
                        if not candidate_ids
                        else "ambiguous_candidate_subject_for_exact_observation"
                    ),
                }
            )
            continue
        decisions_by_subject[candidate_ids[0]].append(payload)

    rows: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for candidate_id, decisions in sorted(decisions_by_subject.items()):
        truths = {
            (row["ground_truth_player_id"], row["ground_truth_team"])
            for row in decisions
        }
        if len(truths) != 1:
            conflicts.append(
                {
                    "candidate_subject_id": candidate_id,
                    "decisions": decisions,
                    "reason": "conflicting_operator_ground_truth",
                }
            )
            continue
        truth_player_id, truth_team = next(iter(truths))
        ranking = ranking_by_subject.get(candidate_id)
        suggestions = list((ranking or {}).get("suggestions") or [])
        ranked_player_ids = [
            str(suggestion.get("player_id") or "")
            for suggestion in suggestions
        ]
        truth_rank = next(
            (
                index
                for index, player_id in enumerate(ranked_player_ids, start=1)
                if player_id == truth_player_id
            ),
            None,
        )
        cross_team_violations = sum(
            player_team_by_id.get(player_id) not in {None, truth_team}
            for player_id in ranked_player_ids
        )
        abstained = not suggestions
        rows.append(
            {
                "candidate_subject_id": candidate_id,
                "observation_provenance": decisions[0],
                "ground_truth_player_id": truth_player_id,
                "ground_truth_team": truth_team,
                "ranked_candidate_player_ids": ranked_player_ids,
                "ranked_distances": [
                    suggestion.get("distance") for suggestion in suggestions
                ],
                "truth_rank": truth_rank,
                "top1_correct": truth_rank == 1,
                "top3_correct": truth_rank is not None and truth_rank <= 3,
                "abstained": abstained,
                "cross_team_violations": cross_team_violations,
            }
        )
    query_count = len(rows)
    ranks = [int(row["truth_rank"]) for row in rows if row["truth_rank"]]
    abstentions = sum(bool(row["abstained"]) for row in rows)
    return {
        "method": "h1_reference_to_h2_operator_ground_truth",
        "ground_truth_source": "operator_confirmed_h2_decisions",
        "ground_truth_used_as_ranking_input": False,
        "queries": query_count,
        "top1_accuracy": _rate(rows, "top1_correct"),
        "top3_accuracy": _rate(rows, "top3_correct"),
        "mean_truth_rank": round(mean(ranks), 4) if ranks else None,
        "median_truth_rank": round(median(ranks), 4) if ranks else None,
        "abstentions": abstentions,
        "cross_team_violations": sum(
            int(row["cross_team_violations"]) for row in rows
        ),
        "rows": rows,
        "unmapped_operator_decisions": unmapped_decisions,
        "conflicting_ground_truth": conflicts,
    }


def build_operator_name_display_gate(
    *,
    model_status: dict[str, Any],
    internal_calibration: dict[str, Any],
    cross_capture_evaluation: dict[str, Any],
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Require real H1→H2 evidence before operator-visible names."""

    params = {**DEFAULT_DISPLAY_GATE, **(parameters or {})}
    reasons: list[str] = []
    if str(model_status.get("quality_tier") or "") == "baseline_fallback":
        reasons.append("baseline_fallback_not_operator_eligible")
    if not model_status.get("selected_runtime"):
        reasons.append("preferred_reid_runtime_unavailable")
    if int(internal_calibration.get("queries") or 0) < int(
        params["minimum_internal_queries"]
    ):
        reasons.append("insufficient_internal_calibration_queries")
    elif float(internal_calibration.get("top1_accuracy") or 0.0) < float(
        params["minimum_internal_top1"]
    ):
        reasons.append("internal_calibration_quality_below_threshold")
    if int(cross_capture_evaluation.get("queries") or 0) < int(
        params["minimum_cross_capture_queries"]
    ):
        reasons.append("insufficient_cross_capture_ground_truth")
    elif (
        float(cross_capture_evaluation.get("top1_accuracy") or 0.0)
        < float(params["minimum_cross_capture_top1"])
        or float(cross_capture_evaluation.get("top3_accuracy") or 0.0)
        < float(params["minimum_cross_capture_top3"])
    ):
        reasons.append("cross_capture_quality_below_threshold")
    if int(cross_capture_evaluation.get("cross_team_violations") or 0):
        reasons.append("cross_team_violation_detected")
    return {
        "parameters": params,
        "display_eligible": not reasons,
        "suppression_reason_codes": reasons,
        "automatic_merges": 0,
    }


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return round(sum(bool(row[key]) for row in rows) / len(rows), 4)
