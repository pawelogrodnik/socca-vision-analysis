from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_approved_appearance_gallery"
ALGORITHM_VERSION = "0.1.0"

DEFAULT_PARAMETERS: dict[str, Any] = {
    "max_crops_per_player_domain": 8,
}


def build_identity_approved_appearance_gallery(
    seeded_assignments_doc: dict[str, Any],
    anchor_crops_doc: dict[str, Any],
    *,
    match_phase_config_doc: dict[str, Any] | None = None,
    generated_at: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build an automatic player gallery from operator-confirmed subjects.

    The operator confirms identity once. This builder reuses the already
    selected reliable crops and never asks for appearance-crop annotation.
    """
    params = {**DEFAULT_PARAMETERS, **(parameters or {})}
    generated = generated_at or datetime.now(timezone.utc).isoformat()
    cards = {
        str(row.get("candidate_subject_id")): row
        for row in anchor_crops_doc.get("cards") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    second_half_start_sec = _second_half_start_time(
        match_phase_config_doc or {}
    )
    accepted_by_player: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unresolved_subject_ids: list[str] = []

    for accepted in seeded_assignments_doc.get("accepted_assignments") or []:
        if not isinstance(accepted, dict):
            continue
        player = accepted.get("assigned_player") or {}
        player_id = str(player.get("player_id") or "")
        subject_id = str(accepted.get("candidate_subject_id") or "")
        if not player_id or not subject_id:
            continue
        card = cards.get(subject_id)
        if card is None:
            unresolved_subject_ids.append(subject_id)
            continue
        accepted_by_player[player_id].append(
            {
                "assignment": accepted,
                "player": player,
                "card": card,
            }
        )

    players = [
        _build_player_gallery(
            player_id,
            rows,
            second_half_start_sec=second_half_start_sec,
            parameters=params,
        )
        for player_id, rows in sorted(accepted_by_player.items())
    ]
    selected_crops = sum(
        len(domain.get("crops") or [])
        for player in players
        for domain in player.get("capture_domains") or []
    )
    cross_domain_players = sum(
        1
        for player in players
        if {"H1", "H2"}.issubset(
            {
                str(domain.get("capture_domain"))
                for domain in player.get("capture_domains") or []
                if domain.get("crops")
            }
        )
    )
    accepted_subjects = {
        str(row.get("candidate_subject_id"))
        for row in seeded_assignments_doc.get("accepted_assignments") or []
        if isinstance(row, dict) and row.get("candidate_subject_id")
    }
    summary = {
        "players": len(players),
        "accepted_candidate_subjects": len(accepted_subjects),
        "candidate_subjects_with_gallery": sum(
            len(player.get("candidate_subject_ids") or [])
            for player in players
        ),
        "selected_crops": selected_crops,
        "h1_players": _domain_player_count(players, "H1"),
        "h2_players": _domain_player_count(players, "H2"),
        "cross_domain_players": cross_domain_players,
        "unresolved_accepted_subjects": len(set(unresolved_subject_ids)),
        "operator_actions_required": 0,
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
        "source": {
            "seeded_assignments_algorithm": (
                seeded_assignments_doc.get("algorithm") or {}
            ),
            "anchor_crops_algorithm": (
                anchor_crops_doc.get("algorithm") or {}
            ),
            "match_phase_algorithm": (
                (match_phase_config_doc or {}).get("algorithm") or None
            ),
            "second_half_start_time_sec": second_half_start_sec,
        },
        "safety": {
            "operator_confirmed_subjects_only": True,
            "automatic_crop_selection": True,
            "operator_labels_appearance_crops": False,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "eligible_for_player_stats": False,
            "automatic_merges": 0,
        },
        "summary": summary,
        "players": players,
        "unresolved_candidate_subject_ids": sorted(
            set(unresolved_subject_ids)
        ),
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_read_only",
        "status": (
            "ready"
            if selected_crops
            else "no_approved_appearance_crops"
        ),
        "algorithm": artifact["algorithm"],
        "summary": summary,
        "gates": {
            "automatic_crop_selection": True,
            "operator_work_did_not_increase": True,
            "production_identity_untouched": True,
            "cross_half_gallery_available": cross_domain_players > 0,
        },
        "limitations": [
            (
                "Viewpoint and lighting are not guessed when the frozen "
                "artifacts do not provide reliable evidence."
            ),
            (
                "A player needs confirmed H1 and H2 subjects before a "
                "cross-domain prototype can be evaluated."
            ),
        ],
    }
    return {
        "identity_approved_appearance_gallery": artifact,
        "identity_approved_appearance_gallery_report": report,
    }


def _build_player_gallery(
    player_id: str,
    rows: list[dict[str, Any]],
    *,
    second_half_start_sec: float | None,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    first = rows[0]
    player = first["player"]
    candidate_subject_ids = sorted(
        {
            str(row["assignment"].get("candidate_subject_id"))
            for row in rows
            if row["assignment"].get("candidate_subject_id")
        }
    )
    domain_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        assignment = row["assignment"]
        card = row["card"]
        subject_id = str(assignment.get("candidate_subject_id") or "")
        seed_stages = sorted(
            {
                str(seed.get("audit_stage") or "initial_identity_audit")
                for seed in assignment.get("seed_observations") or []
                if isinstance(seed, dict)
            }
        )
        for crop in card.get("anchor_crops") or []:
            if not isinstance(crop, dict):
                continue
            domain = _capture_domain(
                crop,
                second_half_start_sec=second_half_start_sec,
                seed_stages=seed_stages,
            )
            bbox = crop.get("bbox_xyxy") or []
            domain_candidates[domain].append(
                {
                    **crop,
                    "candidate_subject_id": subject_id,
                    "capture_domain": domain,
                    "seed_audit_stages": seed_stages,
                    "scale_bucket": _scale_bucket(bbox),
                    "low_occlusion": (
                        "near_occlusion_event"
                        not in set(crop.get("selection_reasons") or [])
                    ),
                    "valid_visual_content": bool(
                        crop.get("selection_eligible")
                    ),
                    "viewpoint": "unknown",
                    "lighting": "unknown",
                }
            )

    capture_domains: list[dict[str, Any]] = []
    for domain, candidates in sorted(domain_candidates.items()):
        selected = _select_player_crops(
            candidates,
            limit=int(parameters["max_crops_per_player_domain"]),
        )
        capture_domains.append(
            {
                "capture_domain": domain,
                "status": "ready" if selected else "no_reliable_crops",
                "candidate_subject_ids": sorted(
                    {
                        str(row.get("candidate_subject_id"))
                        for row in selected
                    }
                ),
                "available_crops": len(candidates),
                "selected_crops": len(selected),
                "crops": selected,
            }
        )
    return {
        "player_id": player_id,
        "player_name": player.get("player_name"),
        "player_number": player.get("player_number"),
        "player_role": player.get("player_role"),
        "team_label": player.get("team_label"),
        "candidate_subject_ids": candidate_subject_ids,
        "capture_domains": capture_domains,
        "automatic_selection": True,
        "operator_review_required": False,
    }


def _select_player_crops(
    candidates: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    deduplicated: dict[tuple[str, int, str], dict[str, Any]] = {}
    for row in candidates:
        key = (
            str(row.get("candidate_subject_id") or ""),
            int(row.get("frame") or 0),
            str(row.get("artifact") or ""),
        )
        existing = deduplicated.get(key)
        if existing is None or _crop_rank(row) < _crop_rank(existing):
            deduplicated[key] = row
    ordered_by_subject: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in deduplicated.values():
        ordered_by_subject[str(row.get("candidate_subject_id"))].append(row)
    for rows in ordered_by_subject.values():
        rows.sort(key=_crop_rank)

    selected: list[dict[str, Any]] = []
    subject_ids = sorted(ordered_by_subject)
    while len(selected) < limit:
        added = False
        for subject_id in subject_ids:
            rows = ordered_by_subject[subject_id]
            if rows and len(selected) < limit:
                selected.append(rows.pop(0))
                added = True
        if not added:
            break
    return sorted(
        selected,
        key=lambda row: (
            int(row.get("frame") or 0),
            str(row.get("candidate_subject_id") or ""),
        ),
    )


def _crop_rank(row: dict[str, Any]) -> tuple[float, int, str]:
    return (
        -float(row.get("selection_score") or 0.0),
        int(row.get("frame") or 0),
        str(row.get("artifact") or ""),
    )


def _capture_domain(
    crop: dict[str, Any],
    *,
    second_half_start_sec: float | None,
    seed_stages: list[str],
) -> str:
    time_sec = crop.get("time_sec")
    if second_half_start_sec is not None and time_sec is not None:
        return (
            "H2"
            if float(time_sec) >= second_half_start_sec
            else "H1"
        )
    if seed_stages and all(
        stage == "second_half_identity_reanchor" for stage in seed_stages
    ):
        return "H2"
    return "H1"


def _scale_bucket(bbox: list[Any]) -> str:
    if len(bbox) != 4:
        return "unknown"
    height = max(0.0, float(bbox[3]) - float(bbox[1]))
    if height < 64:
        return "far"
    if height > 150:
        return "near"
    return "mid"


def _second_half_start_time(
    phase_config: dict[str, Any],
) -> float | None:
    explicit = phase_config.get("second_half_start_time_sec")
    if explicit is not None:
        return float(explicit)
    for period in phase_config.get("periods") or []:
        if not isinstance(period, dict):
            continue
        period_id = str(period.get("period_id") or "").lower()
        if period_id in {"second_half", "2h", "h2"}:
            value = period.get("start_time_sec")
            return float(value) if value is not None else None
    return None


def _domain_player_count(
    players: list[dict[str, Any]],
    domain: str,
) -> int:
    return sum(
        1
        for player in players
        if any(
            row.get("capture_domain") == domain and row.get("crops")
            for row in player.get("capture_domains") or []
        )
    )
