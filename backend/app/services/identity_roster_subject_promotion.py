from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from app.services.identity_roster_subject_review_store import (
    identity_review_artifact_digest,
)


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_roster_subject_promotion_plan"
ALGORITHM_VERSION = "0.1.0"
RESOLVED_DECISIONS = {"confirm_recommended_player", "assign_roster_player"}


def build_identity_roster_subject_promotion_plan(
    review_artifact: dict[str, Any],
    decisions_doc: dict[str, Any],
    candidate_doc: dict[str, Any],
    timeline_doc: dict[str, Any],
    match_doc: dict[str, Any],
    *,
    team_label: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build an exact-frame, read-only promotion plan from operator decisions."""
    normalized_team = str(team_label or "").strip().upper()
    if normalized_team not in {"A", "B"}:
        raise ValueError("team_label must be A or B")

    generated = generated_at or datetime.now(timezone.utc).isoformat()
    review_digest = identity_review_artifact_digest(review_artifact)
    decisions_fresh = decisions_doc.get("source_artifact_digest") == review_digest
    roster = _roster_by_team(match_doc)
    cards = [
        row
        for row in review_artifact.get("cards") or []
        if isinstance(row, dict) and str(row.get("team_label") or "U") == normalized_team
    ]
    cards_by_key = {
        str(row.get("review_card_key")): row
        for row in cards
        if row.get("review_card_key")
    }
    decisions_by_key = {
        str(row.get("review_card_key")): row
        for row in decisions_doc.get("decisions") or []
        if isinstance(row, dict) and row.get("review_card_key") in cards_by_key
    }
    candidates = {
        str(row.get("candidate_subject_id")): row
        for row in candidate_doc.get("subjects") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    timeline = {
        str(row.get("shadow_subject_id")): row
        for row in timeline_doc.get("subjects") or []
        if isinstance(row, dict) and row.get("shadow_subject_id")
    }

    audit = _audit_summary(cards, decisions_by_key)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    unresolved_subjects: list[dict[str, Any]] = []
    resolved_subjects: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []

    if not decisions_fresh:
        errors.append({"code": "stale_operator_decisions"})
    if audit["pending_cards"]:
        errors.append(
            {
                "code": "incomplete_team_audit",
                "pending_cards": audit["pending_cards"],
            }
        )

    for card in sorted(cards, key=_card_sort_key):
        key = str(card.get("review_card_key") or "")
        decision = decisions_by_key.get(key)
        subject_id = str(card.get("candidate_subject_id") or "")
        if decision is None:
            continue
        action = str(decision.get("decision") or "")
        if action == "mark_unresolved":
            unresolved_subjects.append(
                {
                    "review_card_key": key,
                    "candidate_subject_id": subject_id,
                    "start_frame": int(card.get("start_frame") or 0),
                    "end_frame": int(card.get("end_frame") or 0),
                    "comment": decision.get("comment"),
                }
            )
            continue
        if action not in RESOLVED_DECISIONS:
            errors.append(
                {
                    "code": "unsupported_promotion_decision",
                    "review_card_key": key,
                    "decision": action,
                }
            )
            continue
        player_id = str(decision.get("player_id") or "")
        player = roster.get(normalized_team, {}).get(player_id)
        if player is None:
            errors.append(
                {
                    "code": "player_not_in_reviewed_team_roster",
                    "review_card_key": key,
                    "player_id": player_id,
                }
            )
            continue
        candidate = candidates.get(subject_id)
        subject_timeline = timeline.get(subject_id)
        if candidate is None or subject_timeline is None:
            errors.append(
                {
                    "code": "missing_candidate_lineage",
                    "review_card_key": key,
                    "candidate_subject_id": subject_id,
                    "candidate_found": candidate is not None,
                    "timeline_found": subject_timeline is not None,
                }
            )
            continue
        lineage_errors = _lineage_errors(card, candidate, subject_timeline, normalized_team)
        if lineage_errors:
            errors.extend(
                {
                    "code": code,
                    "review_card_key": key,
                    "candidate_subject_id": subject_id,
                }
                for code in lineage_errors
            )
            continue

        subject_observations = _subject_observations(
            subject_timeline,
            review_card_key=key,
            candidate_subject_id=subject_id,
            player_id=player_id,
        )
        if not subject_observations:
            errors.append(
                {
                    "code": "resolved_subject_has_no_exact_observations",
                    "review_card_key": key,
                    "candidate_subject_id": subject_id,
                }
            )
            continue
        observations.extend(subject_observations)
        resolved_subjects.append(
            {
                "review_card_key": key,
                "candidate_subject_id": subject_id,
                "candidate_player_id": candidate.get("candidate_player_id"),
                "player_id": player_id,
                "player_name": player.get("name") or player_id,
                "decision": action,
                "start_frame": int(candidate.get("start_frame") or 0),
                "end_frame": int(candidate.get("end_frame") or 0),
                "exact_detected_frames": len(subject_observations),
                "tracklet_ids": sorted(str(value) for value in candidate.get("tracklet_ids") or []),
                "production_subject_ids": sorted(
                    str(value) for value in candidate.get("production_subject_ids") or []
                ),
            }
        )

    canonical, duplicates, hard_conflicts = _canonicalize_observations(observations)
    errors.extend(hard_conflicts)
    if duplicates:
        warnings.append(
            {
                "code": "parallel_subject_observations_deduplicated",
                "duplicate_observations": len(duplicates),
                "affected_players": len({row["player_id"] for row in duplicates}),
            }
        )
    coverage = _coverage_by_player(canonical, roster.get(normalized_team, {}))
    recommendation = _recommendation_metrics(cards_by_key, decisions_by_key)
    status = "ready_for_controlled_apply" if not errors else "blocked"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_promotion_plan",
        "status": status,
        "algorithm": {
            "name": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
        },
        "source": {
            "review_artifact_digest": review_digest,
            "decisions_artifact_digest": _document_digest(decisions_doc),
            "candidate_artifact_digest": _document_digest(candidate_doc),
            "timeline_artifact_digest": _document_digest(timeline_doc),
            "team_label": normalized_team,
        },
        "safety": {
            "writes_plan_only": True,
            "writes_player_identity_assignments": False,
            "mutates_production_identity": False,
            "rebuilds_statistics": False,
            "rebuilds_heatmaps": False,
            "requires_explicit_apply_step": True,
        },
        "audit": {
            **audit,
            "decisions_fresh": decisions_fresh,
            "recommendation_metrics": recommendation,
        },
        "summary": {
            "resolved_subjects": len(resolved_subjects),
            "unresolved_subjects": len(unresolved_subjects),
            "source_observations": len(observations),
            "canonical_observations": len(canonical),
            "duplicate_observations_removed": len(duplicates),
            "hard_conflicts": len(hard_conflicts),
            "players_with_coverage": len(coverage),
            "blocking_errors": len(errors),
            "warnings": len(warnings),
        },
        "resolved_subjects": resolved_subjects,
        "unresolved_subjects": unresolved_subjects,
        "canonical_coverage": coverage,
        "duplicate_observations": duplicates,
        "errors": errors,
        "warnings": warnings,
    }


def _audit_summary(
    cards: list[dict[str, Any]],
    decisions_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    actions = Counter(
        str(row.get("decision") or "unknown") for row in decisions_by_key.values()
    )
    return {
        "team_cards": len(cards),
        "reviewed_cards": len(decisions_by_key),
        "pending_cards": max(0, len(cards) - len(decisions_by_key)),
        "decision_counts": dict(sorted(actions.items())),
    }


def _recommendation_metrics(
    cards_by_key: dict[str, dict[str, Any]],
    decisions_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    reviewed = 0
    accepted = 0
    rejected = 0
    for key, decision in decisions_by_key.items():
        recommended = str(
            ((cards_by_key.get(key) or {}).get("recommended_player") or {}).get("player_id") or ""
        )
        if not recommended:
            continue
        reviewed += 1
        if str(decision.get("player_id") or "") == recommended:
            accepted += 1
        elif decision.get("decision") != "mark_unresolved":
            rejected += 1
    return {
        "reviewed_recommendations": reviewed,
        "accepted_recommendations": accepted,
        "rejected_recommendations": rejected,
        "precision": round(accepted / reviewed, 6) if reviewed else None,
    }


def _lineage_errors(
    card: dict[str, Any],
    candidate: dict[str, Any],
    timeline: dict[str, Any],
    team_label: str,
) -> list[str]:
    errors: list[str] = []
    if str(candidate.get("team_label") or "U") != team_label:
        errors.append("candidate_team_mismatch")
    if str(timeline.get("team_label") or "U") != team_label:
        errors.append("timeline_team_mismatch")
    card_range = (int(card.get("start_frame") or 0), int(card.get("end_frame") or 0))
    candidate_range = (
        int(candidate.get("start_frame") or 0),
        int(candidate.get("end_frame") or 0),
    )
    timeline_range = (
        int(timeline.get("start_frame") or 0),
        int(timeline.get("end_frame") or 0),
    )
    if card_range != candidate_range:
        errors.append("review_candidate_range_mismatch")
    if candidate_range != timeline_range:
        errors.append("candidate_timeline_range_mismatch")
    candidate_tracklets = {str(value) for value in candidate.get("tracklet_ids") or []}
    timeline_tracklets = {str(value) for value in timeline.get("tracklet_ids") or []}
    if candidate_tracklets != timeline_tracklets:
        errors.append("candidate_timeline_tracklet_mismatch")
    return errors


def _subject_observations(
    timeline: dict[str, Any],
    *,
    review_card_key: str,
    candidate_subject_id: str,
    player_id: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for observation in timeline.get("observations") or []:
        if not isinstance(observation, dict) or observation.get("frame") is None:
            continue
        rows.append(
            {
                "frame": int(observation["frame"]),
                "time_sec": round(float(observation.get("time_sec") or 0.0), 6),
                "player_id": player_id,
                "review_card_key": review_card_key,
                "candidate_subject_id": candidate_subject_id,
                "tracklet_id": str(observation.get("tracklet_id") or ""),
                "status": str(observation.get("status") or "detected"),
                "confidence": round(float(observation.get("confidence") or 0.0), 6),
                "play_area_status": observation.get("play_area_status"),
                "eligible_for_distance": bool(observation.get("eligible_for_distance")),
                "eligible_for_heatmap": bool(observation.get("eligible_for_heatmap")),
            }
        )
    return rows


def _canonicalize_observations(
    observations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_source: dict[tuple[int, str], set[str]] = defaultdict(set)
    source_subjects: dict[tuple[int, str], set[str]] = defaultdict(set)
    for row in observations:
        source_key = (int(row["frame"]), str(row["tracklet_id"]))
        by_source[source_key].add(str(row["player_id"]))
        source_subjects[source_key].add(str(row["candidate_subject_id"]))
    hard_conflicts = [
        {
            "code": "same_source_observation_maps_to_multiple_players",
            "frame": frame,
            "tracklet_id": tracklet_id,
            "player_ids": sorted(players),
            "candidate_subject_ids": sorted(source_subjects[(frame, tracklet_id)]),
        }
        for (frame, tracklet_id), players in sorted(by_source.items())
        if len(players) > 1
    ]

    by_player_frame: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        by_player_frame[(str(row["player_id"]), int(row["frame"]))].append(row)
    canonical: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for (player_id, frame), rows in sorted(by_player_frame.items()):
        ranked = sorted(rows, key=_observation_rank)
        winner = ranked[0]
        canonical.append(winner)
        if len(ranked) > 1:
            duplicates.append(
                {
                    "player_id": player_id,
                    "frame": frame,
                    "kept_candidate_subject_id": winner["candidate_subject_id"],
                    "removed_candidate_subject_ids": sorted(
                        str(row["candidate_subject_id"]) for row in ranked[1:]
                    ),
                }
            )
    return canonical, duplicates, hard_conflicts


def _observation_rank(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        0 if row.get("status") == "detected" else 1,
        0 if row.get("play_area_status") == "inside_play" else 1,
        0 if row.get("eligible_for_distance") else 1,
        -float(row.get("confidence") or 0.0),
        str(row.get("candidate_subject_id") or ""),
        str(row.get("tracklet_id") or ""),
    )


def _coverage_by_player(
    observations: list[dict[str, Any]],
    roster: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        by_player[str(row["player_id"])].append(row)
    result: list[dict[str, Any]] = []
    for player_id, rows in sorted(by_player.items()):
        rows.sort(key=lambda row: (int(row["frame"]), str(row["candidate_subject_id"])))
        result.append(
            {
                "player_id": player_id,
                "player_name": (roster.get(player_id) or {}).get("name") or player_id,
                "unique_detected_frames": len(rows),
                "distance_eligible_frames": sum(bool(row["eligible_for_distance"]) for row in rows),
                "heatmap_eligible_frames": sum(bool(row["eligible_for_heatmap"]) for row in rows),
                "frame_intervals": _frame_intervals(rows),
                "frame_records": rows,
            }
        )
    return result


def _frame_intervals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    intervals: list[dict[str, Any]] = []
    for row in rows:
        frame = int(row["frame"])
        key = (str(row["candidate_subject_id"]), str(row["tracklet_id"]))
        previous = intervals[-1] if intervals else None
        if previous and previous["end_frame"] + 1 == frame and previous["source_key"] == key:
            previous["end_frame"] = frame
            previous["frames"] += 1
            continue
        intervals.append(
            {
                "start_frame": frame,
                "end_frame": frame,
                "frames": 1,
                "candidate_subject_id": key[0],
                "tracklet_id": key[1],
                "source_key": key,
            }
        )
    for interval in intervals:
        interval.pop("source_key", None)
    return intervals


def _roster_by_team(match_doc: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {"A": {}, "B": {}}
    for index, team in enumerate(match_doc.get("teams") or []):
        label = "A" if index == 0 else "B" if index == 1 else None
        if label is None or not isinstance(team, dict):
            continue
        result[label] = {
            str(player["id"]): player
            for player in team.get("players") or []
            if isinstance(player, dict) and player.get("id")
        }
    return result


def _card_sort_key(card: dict[str, Any]) -> tuple[int, str]:
    return (int(card.get("start_frame") or 0), str(card.get("candidate_subject_id") or ""))


def _document_digest(document: dict[str, Any]) -> str:
    payload = json.dumps(
        _without_generated_at(document),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _without_generated_at(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_generated_at(item)
            for key, item in value.items()
            if key not in {"generated_at", "updated_at"}
        }
    if isinstance(value, list):
        return [_without_generated_at(item) for item in value]
    return value
