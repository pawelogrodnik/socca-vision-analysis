from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from app.services.identity_roster_subject_review_store import (
    identity_review_artifact_digest,
)
from app.services.identity_promotion_safety import (
    DEFAULT_PARAMETERS,
    build_promotion_safety_sections,
    canonical_document_digest,
    canonicalize_promoted_observations,
    structural_conflict_reasons,
)


SCHEMA_VERSION = "0.2.0"
ALGORITHM_NAME = "identity_roster_subject_promotion_plan"
ALGORITHM_VERSION = "0.2.0"
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
    anchor_crops_doc: dict[str, Any] | None = None,
    team_config_doc: dict[str, Any] | None = None,
    review_contract_doc: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an exact-frame promotion safety plan without mutating production identity."""
    normalized_team = str(team_label or "").strip().upper()
    if normalized_team not in {"A", "B"}:
        raise ValueError("team_label must be A or B")

    generated = generated_at or datetime.now(timezone.utc).isoformat()
    algorithm_parameters = {**DEFAULT_PARAMETERS, **(parameters or {})}
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
    all_review_observations: list[dict[str, Any]] = []
    unresolved_observations: list[dict[str, Any]] = []
    structural_subjects: list[dict[str, Any]] = []

    if not decisions_fresh:
        errors.append({"code": "stale_operator_decisions"})
    if audit["pending_cards"]:
        errors.append(
            {
                "code": "incomplete_team_audit",
                "pending_cards": audit["pending_cards"],
            }
        )

    lineage = _lineage_status(
        review_artifact,
        candidate_doc,
        timeline_doc,
        match_doc,
        anchor_crops_doc=anchor_crops_doc,
        team_config_doc=team_config_doc,
        review_contract_doc=review_contract_doc,
        decisions_doc=decisions_doc,
        parameters=algorithm_parameters,
    )
    errors.extend(lineage["errors"])
    warnings.extend(lineage["warnings"])

    for card in sorted(cards, key=_card_sort_key):
        key = str(card.get("review_card_key") or "")
        decision = decisions_by_key.get(key)
        subject_id = str(card.get("candidate_subject_id") or "")
        candidate = candidates.get(subject_id)
        subject_timeline = timeline.get(subject_id)
        if candidate is not None and subject_timeline is not None:
            card_observations = _subject_observations(
                subject_timeline,
                review_card_key=key,
                candidate_subject_id=subject_id,
                player_id="",
                candidate=candidate,
            )
            all_review_observations.extend(card_observations)
        else:
            card_observations = []
        if not _is_actionable_review_card(card):
            unresolved_subjects.append(
                {
                    "review_card_key": key,
                    "candidate_subject_id": subject_id,
                    "start_frame": int(card.get("start_frame") or 0),
                    "end_frame": int(card.get("end_frame") or 0),
                    "reason": str(card.get("review_status") or "not_actionable"),
                    "comment": "Excluded because the review card has insufficient visual evidence.",
                }
            )
            unresolved_observations.extend(card_observations)
            continue
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
            unresolved_observations.extend(card_observations)
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
        structural_reasons = structural_conflict_reasons(card, candidate, subject_timeline)
        if structural_reasons:
            structural_subjects.append(
                {
                    "review_card_key": key,
                    "candidate_subject_id": subject_id,
                    "player_id": player_id,
                    "start_frame": int(candidate.get("start_frame") or 0),
                    "end_frame": int(candidate.get("end_frame") or 0),
                    "tracklet_ids": sorted(str(value) for value in candidate.get("tracklet_ids") or []),
                    "reasons": structural_reasons,
                    "required_action": "remediate_or_exclude_before_partial_candidate",
                    "frame_records": card_observations,
                }
            )
            unresolved_observations.extend(card_observations)
            errors.append(
                {
                    "code": "structural_conflict_whole_subject_assignment_blocked",
                    "review_card_key": key,
                    "candidate_subject_id": subject_id,
                    "reasons": structural_reasons,
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
            candidate=candidate,
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
                "exact_detected_frames": sum(
                    str(row.get("status") or "") == "detected" for row in subject_observations
                ),
                "tracklet_ids": sorted(str(value) for value in candidate.get("tracklet_ids") or []),
                "production_subject_ids": sorted(
                    str(value) for value in candidate.get("production_subject_ids") or []
                ),
            }
        )

    canonical, duplicates, hard_conflicts = canonicalize_promoted_observations(
        observations,
        parameters=algorithm_parameters,
    )
    errors.extend(hard_conflicts)
    if duplicates:
        warnings.append(
            {
                "code": "parallel_subject_observations_deduplicated",
                "duplicate_observations": len(duplicates),
                "affected_players": len({row["player_id"] for row in duplicates}),
            }
        )
    fps = _match_fps(match_doc)
    safety_sections = build_promotion_safety_sections(
        canonical_observations=canonical,
        all_review_observations=all_review_observations,
        unresolved_observations=unresolved_observations,
        structural_subjects=structural_subjects,
        roster=roster.get(normalized_team, {}),
        match_doc=match_doc,
        team_label=normalized_team,
        fps=fps,
        parameters=algorithm_parameters,
    )
    errors.extend(safety_sections["errors"])
    warnings.extend(safety_sections["warnings"])
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
            "parameters": algorithm_parameters,
        },
        "source": {
            **lineage["digests"],
            "review_artifact_digest": review_digest,
            "team_label": normalized_team,
            "freshness": lineage["freshness"],
        },
        "safety": {
            "writes_plan_only": True,
            "writes_player_identity_assignments": False,
            "mutates_production_identity": False,
            "rebuilds_statistics": False,
            "rebuilds_heatmaps": False,
            "requires_explicit_apply_step": True,
            "structural_remediation_required": bool(structural_subjects),
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
            "structural_conflicts": len(structural_subjects),
            "players_with_coverage": len(coverage),
            "blocking_errors": len(errors),
            "warnings": len(warnings),
        },
        "resolved_subjects": resolved_subjects,
        "unresolved_subjects": unresolved_subjects,
        "canonical_coverage": coverage,
        "coverage": safety_sections["coverage"],
        "player_readiness": safety_sections["player_readiness"],
        "active_player_validation": safety_sections["active_player_validation"],
        "goalkeeper_validation": safety_sections["goalkeeper_validation"],
        "downstream_readiness": safety_sections["downstream_readiness"],
        "structural_subjects": structural_subjects,
        "duplicate_observations": duplicates,
        "errors": errors,
        "warnings": warnings,
    }


def _audit_summary(
    cards: list[dict[str, Any]],
    decisions_by_key: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    actionable_cards = [card for card in cards if _is_actionable_review_card(card)]
    actionable_keys = {
        str(card.get("review_card_key") or "")
        for card in actionable_cards
        if card.get("review_card_key")
    }
    actionable_decisions = {
        key: row for key, row in decisions_by_key.items() if key in actionable_keys
    }
    actions = Counter(
        str(row.get("decision") or "unknown") for row in actionable_decisions.values()
    )
    return {
        "team_cards": len(cards),
        "actionable_cards": len(actionable_cards),
        "non_actionable_cards": len(cards) - len(actionable_cards),
        "reviewed_cards": len(actionable_decisions),
        "pending_cards": max(0, len(actionable_cards) - len(actionable_decisions)),
        "ignored_non_actionable_decisions": len(decisions_by_key) - len(actionable_decisions),
        "decision_counts": dict(sorted(actions.items())),
    }


def _is_actionable_review_card(card: dict[str, Any]) -> bool:
    review_status = str(card.get("review_status") or "").strip()
    return not review_status or review_status == "ready_for_operator_review"


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
    candidate: dict[str, Any],
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
                "pitch_m": observation.get("pitch_m"),
                "bbox_xyxy": observation.get("bbox_xyxy"),
                "footpoint_reliable": observation.get("footpoint_reliable"),
                "appearance_reliable": observation.get("appearance_reliable"),
                "quality_class": observation.get("quality_class"),
                "role": candidate.get("role"),
                "subject_start_frame": int(candidate.get("start_frame") or 0),
                "subject_end_frame": int(candidate.get("end_frame") or 0),
                "production_subject_ids": sorted(
                    str(value) for value in candidate.get("production_subject_ids") or []
                ),
                "structural_reasons": sorted(
                    str(value)
                    for value in candidate.get("quality_flags") or []
                    if str(value) in {
                        "merges_production_subjects",
                        "merges_multiple_production_subjects",
                        "cross_production_transition",
                        "uncertain_transition",
                        "parallel_roster_candidate_conflict",
                        "parallel_subject_observations",
                        "mixed_team_evidence",
                        "structural_identity_conflict",
                    }
                ),
            }
        )
    return rows


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
    return canonical_document_digest(document)


def _lineage_status(
    review_artifact: dict[str, Any],
    candidate_doc: dict[str, Any],
    timeline_doc: dict[str, Any],
    match_doc: dict[str, Any],
    *,
    anchor_crops_doc: dict[str, Any] | None,
    team_config_doc: dict[str, Any] | None,
    review_contract_doc: dict[str, Any] | None,
    decisions_doc: dict[str, Any],
    parameters: dict[str, Any],
) -> dict[str, Any]:
    documents = {
        "candidate_artifact": candidate_doc,
        "timeline_artifact": timeline_doc,
        "anchor_crops_artifact": anchor_crops_doc or {},
        "match_configuration": match_doc,
        "team_configuration": team_config_doc or {},
        "review_contract": review_contract_doc or {},
        "decisions_artifact": decisions_doc,
        "algorithm_parameters": parameters,
    }
    digests = {f"{name}_digest": _document_digest(document) for name, document in documents.items()}
    source = review_artifact.get("source") if isinstance(review_artifact.get("source"), dict) else {}
    aliases = {
        "candidate_artifact": ("candidate_artifact_digest", "candidate_identity_artifact_digest"),
        "timeline_artifact": ("timeline_artifact_digest", "shadow_timeline_artifact_digest"),
        "anchor_crops_artifact": ("anchor_crops_artifact_digest",),
        "match_configuration": ("match_configuration_digest", "match_artifact_digest"),
        "team_configuration": ("team_configuration_digest",),
        "review_contract": ("review_contract_digest",),
    }
    checked = 0
    errors: list[dict[str, Any]] = []
    for name, keys in aliases.items():
        expected = next((source.get(key) for key in keys if source.get(key)), None)
        if expected is None:
            continue
        checked += 1
        actual = digests[f"{name}_digest"]
        if expected != actual:
            errors.append(
                {
                    "code": "stale_lineage_digest",
                    "source": name,
                    "expected_digest": expected,
                    "actual_digest": actual,
                }
            )
    freshness = "stale" if errors else "fresh" if checked == len(aliases) else "legacy_unknown"
    warnings = []
    if freshness == "legacy_unknown":
        warnings.append(
            {
                "code": "legacy_review_lineage_incomplete",
                "checked_digests": checked,
                "required_digests": len(aliases),
            }
        )
    return {"digests": digests, "freshness": freshness, "errors": errors, "warnings": warnings}


def _match_fps(match_doc: dict[str, Any]) -> float:
    video = match_doc.get("video") if isinstance(match_doc.get("video"), dict) else {}
    for value in (video.get("fps"), match_doc.get("fps")):
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return 30.0
