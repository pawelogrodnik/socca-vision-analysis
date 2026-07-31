from __future__ import annotations

"""Bounded, leakage-safe H2 ground-truth collection for preferred ReID."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from app.services.identity_jersey_number_common import canonical_digest
from app.services.identity_cross_capture_reid_validation import (
    build_operator_name_display_gate,
)
from app.services.identity_second_half_reanchor import (
    REANCHOR_DIRECTORY,
    SELECTION_FILENAME,
    build_second_half_identity_reanchor_document,
)


SCHEMA_VERSION = "1.0.0"
SESSION_ID = "product-flow-20260730-v5-reid-followup"
MAXIMUM_CARDS = 5
ALLOWED_ACTIONS = {
    "player",
    "unknown",
    "skip",
    "bad_bbox",
    "wrong_team",
}


def prepare_bounded_h2_reid_followup(
    *,
    source_root: Path,
    session_root: Path,
    source_commit: str,
) -> dict[str, Any]:
    """Freeze preferred rankings before creating an empty operator store."""

    session_path = session_root / SESSION_ID
    selection_path = session_path / "bounded_h2_selection.json"
    if selection_path.is_file():
        return load_bounded_h2_reid_followup(session_path)

    source_h2 = source_root / "h2_workspace"
    diagnostic = source_root / "cross_capture_reid_diagnostic"
    reid = _load(
        diagnostic / "h2" / "identity_cross_analysis_appearance_reid.json"
    )
    validation = _load(diagnostic / "cross_capture_reid_validation.json")
    preferred = (reid.get("diagnostic_models") or {}).get("preferred") or {}
    if preferred.get("status") != "completed":
        raise ValueError("Preferred ReID replay is not complete")
    candidate = _load(source_h2 / "identity_candidate_shadow.json")
    crops = _load(source_h2 / "identity_roster_anchor_crops_shadow.json")
    source_selection = _load(
        source_h2 / REANCHOR_DIRECTORY / SELECTION_FILENAME
    )
    match = _load(source_h2 / "match.json")
    audit_document = build_second_half_identity_reanchor_document(
        source_selection,
        match,
    )
    candidate_by_id = {
        str(row.get("candidate_subject_id") or ""): row
        for row in candidate.get("subjects") or []
    }
    crop_by_subject = {
        str(row.get("candidate_subject_id") or ""): row
        for row in crops.get("cards") or []
    }
    observations_by_tracklet = {
        str((observation.get("provenance") or {}).get("tracklet_id") or ""): {
            **observation,
            "frame_artifact": frame.get("full_frame_artifact"),
            "frame": frame.get("frame_number"),
            "time_sec": frame.get("time_sec"),
        }
        for frame in audit_document.get("frames") or []
        for observation in frame.get("observations") or []
    }
    existing_subjects = {
        str(row.get("candidate_subject_id") or "")
        for row in (
            validation.get("preferred_cross_capture_evaluation") or {}
        ).get("rows")
        or []
    }
    eligible = []
    for ranking in preferred.get("rankings") or []:
        subject_id = str(ranking.get("candidate_subject_id") or "")
        if (
            ranking.get("status") != "ranked"
            or subject_id in existing_subjects
        ):
            continue
        subject = candidate_by_id.get(subject_id) or {}
        mapped = [
            observations_by_tracklet.get(str(tracklet_id))
            for tracklet_id in subject.get("tracklet_ids") or []
        ]
        observation = next((row for row in mapped if row), None)
        crop_card = crop_by_subject.get(subject_id) or {}
        anchor_crops = sorted(
            crop_card.get("anchor_crops") or [],
            key=lambda row: (
                -float(row.get("selection_score") or 0.0),
                int(row.get("frame") or 0),
            ),
        )
        if observation is None or not anchor_crops:
            continue
        suggestions = list(ranking.get("suggestions") or [])
        margin = (
            float(suggestions[1]["distance"])
            - float(suggestions[0]["distance"])
            if len(suggestions) > 1
            else 1.0
        )
        eligible.append(
            {
                "ranking": ranking,
                "subject": subject,
                "observation": observation,
                "crop": anchor_crops[0],
                "selection_score": (
                    margin,
                    -float(anchor_crops[0].get("selection_score") or 0.0),
                    int(observation.get("frame") or 0),
                    subject_id,
                ),
            }
        )
    selected = _select_temporally_diverse(eligible)
    if len(selected) < 4:
        raise ValueError("Fewer than four independent H2 subjects are available")

    session_path.mkdir(parents=True, exist_ok=False)
    (session_path / "frames").mkdir()
    (session_path / "crops").mkdir()
    cards = []
    frozen_rankings = []
    for index, row in enumerate(selected, start=1):
        subject_id = str(row["subject"]["candidate_subject_id"])
        observation = row["observation"]
        frame = int(observation["frame"])
        source_frame = (
            source_h2
            / REANCHOR_DIRECTORY
            / "frames"
            / f"frame-{frame:06d}.jpg"
        )
        frame_artifact = f"frames/frame-{frame:06d}.jpg"
        shutil.copy2(source_frame, session_path / frame_artifact)
        source_crop = source_h2 / str(row["crop"]["artifact"])
        crop_artifact = f"crops/card-{index:02d}.jpg"
        shutil.copy2(source_crop, session_path / crop_artifact)
        provenance = observation.get("provenance") or {}
        exact_mapping = {
            "candidate_subject_id": subject_id,
            "observation_key": observation.get("observation_key"),
            "frame": frame,
            "tracklet_id": provenance.get("tracklet_id"),
            "bbox_xyxy": observation.get("bbox_xyxy"),
            "source_artifact_digest": _file_sha256(source_frame),
        }
        display_crop_observation = {
            "anchor_crop_id": row["crop"].get("anchor_crop_id"),
            "frame": row["crop"].get("frame"),
            "tracklet_id": row["crop"].get("tracklet_id"),
            "bbox_xyxy": row["crop"].get("bbox_xyxy"),
            "artifact": crop_artifact,
            "source_artifact": row["crop"].get("artifact"),
        }
        cards.append(
            {
                "card_id": f"bounded-h2-{index:02d}",
                **exact_mapping,
                "decision_observation": {
                    **exact_mapping,
                    "full_frame_artifact": frame_artifact,
                },
                "display_crop_observation": display_crop_observation,
                "team_label": row["ranking"].get("team_label"),
                "frame_artifact": frame_artifact,
                "crop_artifact": crop_artifact,
                "frame_width": int(
                    (source_selection.get("video") or {}).get("width") or 1920
                ),
                "frame_height": int(
                    (source_selection.get("video") or {}).get("height") or 1080
                ),
                "time_sec": observation.get("time_sec"),
                "preferred_advisory": {
                    "available": True,
                    "visible": False,
                    "reason": "operator_name_quality_gate_not_passed",
                    "model_name": (
                        preferred.get("model") or {}
                    ).get("model_name"),
                    "runtime": (
                        preferred.get("model") or {}
                    ).get("runtime"),
                    "top3": [],
                },
            }
        )
        frozen_rankings.append(
            {
                **exact_mapping,
                "team_label": row["ranking"].get("team_label"),
                "suggestions": list(row["ranking"].get("suggestions") or [])[:3],
                "advisory_only": True,
                "operator_visible": False,
            }
        )

    ranking_artifact = {
        "schema_version": SCHEMA_VERSION,
        "mode": "bounded_h2_preferred_ranking_freeze",
        "source_preferred_artifact_digest": preferred.get("artifact_digest"),
        "model": preferred.get("model") or {},
        "rankings": frozen_rankings,
        "safety": {
            "ground_truth_used_as_ranking_input": False,
            "portable_ranking_included": False,
            "operator_visible": False,
        },
    }
    ranking_digest = canonical_digest(ranking_artifact)
    _write(session_path / "preferred_rankings_frozen.json", ranking_artifact)

    source_digests = {
        "source_v4_benchmark": source_root.name,
        "source_commit_sha": source_commit,
        "h1_gallery_digest": canonical_digest(
            _load(
                diagnostic
                / "h1"
                / "identity_approved_appearance_gallery.json"
            )
        ),
        "h2_source_digest": canonical_digest(
            {
                "candidate": candidate,
                "crops": crops,
                "selection": source_selection,
            }
        ),
        "preferred_model_xml_sha256": (
            (validation.get("runtime_probe") or {})
            .get("rosetta_runtime_probe", {})
            .get("model", {})
            .get("xml_sha256")
        ),
        "preferred_model_bin_sha256": (
            (validation.get("runtime_probe") or {})
            .get("rosetta_runtime_probe", {})
            .get("model", {})
            .get("bin_sha256")
        ),
        "runtime_version": (
            (validation.get("runtime_probe") or {})
            .get("rosetta_runtime_probe", {})
            .get("runtime_details", {})
            .get("openvino_version")
        ),
        "runtime_manifest_digest": (
            (validation.get("runtime_probe") or {})
            .get("rosetta_runtime_probe", {})
            .get("runtime_manifest_digest")
        ),
        "preprocessing_contract_digest": canonical_digest(
            (
                (preferred.get("model") or {}).get("cache_namespace") or {}
            ).get("preprocessing_version")
        ),
        "preferred_ranking_digest": ranking_digest,
    }
    selection = {
        "schema_version": SCHEMA_VERSION,
        "mode": "bounded_h2_reid_followup",
        "session_id": SESSION_ID,
        "status": "BOUNDED_H2_OPERATOR_INPUT_REQUIRED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": source_digests,
        "ranking_digest": ranking_digest,
        "selection_policy": {
            "maximum_cards": MAXIMUM_CARDS,
            "uses_ground_truth_identity": False,
            "uses_historical_player_assignment": False,
            "uses_desired_gate_outcome": False,
            "criteria": [
                "preferred_ranking_available",
                "exact_observation_mapping",
                "crop_quality",
                "ranking_uncertainty",
                "temporal_diversity",
            ],
        },
        "cards": cards,
        "roster": audit_document.get("roster") or [],
        "safety": _safety(),
    }
    selection["selection_digest"] = canonical_digest(selection)
    _write(selection_path, selection)
    _write(
        session_path / "operator_decisions.json",
        {
            "schema_version": SCHEMA_VERSION,
            "session_id": SESSION_ID,
            "selection_digest": selection["selection_digest"],
            "ranking_digest": ranking_digest,
            "status": "open",
            "decisions": [],
            "finished": False,
            "safety": _safety(),
        },
    )
    return load_bounded_h2_reid_followup(session_path)


def load_bounded_h2_reid_followup(session_path: Path) -> dict[str, Any]:
    selection = _load(session_path / "bounded_h2_selection.json")
    decisions = _load(session_path / "operator_decisions.json")
    evaluation_path = session_path / "bounded_h2_evaluation.json"
    return {
        **selection,
        "decisions": decisions.get("decisions") or [],
        "finished": bool(decisions.get("finished")),
        "operator_decisions_present": bool(decisions.get("decisions")),
        "evaluation": (
            _load(evaluation_path) if evaluation_path.is_file() else None
        ),
    }


def save_bounded_h2_reid_decisions(
    session_path: Path,
    *,
    updates: list[dict[str, Any]],
    finished: bool = False,
) -> dict[str, Any]:
    selection = _load(session_path / "bounded_h2_selection.json")
    store_path = session_path / "operator_decisions.json"
    store = _load(store_path)
    cards = {
        str(row.get("candidate_subject_id") or ""): row
        for row in selection.get("cards") or []
    }
    roster = {
        str(player.get("player_id") or ""): {
            **player,
            "team_label": team.get("team_label"),
        }
        for team in selection.get("roster") or []
        for player in team.get("players") or []
    }
    decisions = {
        str(row.get("candidate_subject_id") or ""): row
        for row in store.get("decisions") or []
    }
    for update in updates:
        subject_id = str(update.get("candidate_subject_id") or "")
        card = cards.get(subject_id)
        if card is None:
            raise ValueError("Unknown bounded H2 subject")
        if update.get("selection_digest") != selection["selection_digest"]:
            raise ValueError("Bounded H2 selection digest mismatch")
        for key in ("observation_key", "frame", "tracklet_id"):
            if update.get(key) != card.get(key):
                raise ValueError(f"Bounded H2 exact mapping mismatch: {key}")
        action = str(update.get("action") or "")
        if action not in ALLOWED_ACTIONS:
            raise ValueError("Invalid bounded H2 action")
        player = None
        if action == "player":
            player = roster.get(str(update.get("player_id") or ""))
            if player is None:
                raise ValueError("Unknown roster player")
            if str(player.get("team_label")) != str(card.get("team_label")):
                raise ValueError("Cross-team player decision is forbidden")
        decisions[subject_id] = {
            "candidate_subject_id": subject_id,
            "observation_key": card.get("observation_key"),
            "frame": card.get("frame"),
            "tracklet_id": card.get("tracklet_id"),
            "bbox_xyxy": card.get("bbox_xyxy"),
            "source_artifact_digest": card.get("source_artifact_digest"),
            "selection_digest": selection["selection_digest"],
            "ranking_digest": selection["ranking_digest"],
            "action": action,
            "player_id": player.get("player_id") if player else None,
            "player_name": player.get("player_name") if player else None,
            "team_label": card.get("team_label"),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
    if finished:
        missing_subjects = sorted(set(cards) - set(decisions))
        if missing_subjects:
            raise ValueError(
                "Cannot finish bounded H2 session with missing card decisions: "
                + ", ".join(missing_subjects)
            )
    next_store = {
        **store,
        "decisions": sorted(
            decisions.values(),
            key=lambda row: str(row["candidate_subject_id"]),
        ),
        "finished": bool(finished),
        "status": "finished" if finished else "open",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "safety": _safety(),
    }
    _write(store_path, next_store)
    return load_bounded_h2_reid_followup(session_path)


def evaluate_bounded_h2_reid_followup(
    *,
    source_root: Path,
    session_path: Path,
) -> dict[str, Any]:
    selection = _load(session_path / "bounded_h2_selection.json")
    decisions = _load(session_path / "operator_decisions.json")
    if not decisions.get("finished"):
        raise ValueError("Bounded H2 operator session is not finished")
    frozen = _load(session_path / "preferred_rankings_frozen.json")
    if canonical_digest(frozen) != selection.get("ranking_digest"):
        raise ValueError("Frozen preferred ranking digest mismatch")
    frozen_by_subject = {
        str(row.get("candidate_subject_id") or ""): row
        for row in frozen.get("rankings") or []
    }
    ranking_verification = verify_frozen_bounded_h2_rankings(selection, frozen)
    verification_by_subject = {
        str(row.get("candidate_subject_id") or ""): row
        for row in ranking_verification["rows"]
    }
    new_rows = []
    action_counts = {
        "unknown": 0,
        "skip": 0,
        "bad_bbox": 0,
        "wrong_team": 0,
        "unmapped": 0,
        "conflicts": 0,
    }
    for decision in decisions.get("decisions") or []:
        action = str(decision.get("action") or "")
        if action != "player":
            if action in action_counts:
                action_counts[action] += 1
            continue
        frozen_row = frozen_by_subject.get(
            str(decision.get("candidate_subject_id") or "")
        )
        if frozen_row is None:
            action_counts["unmapped"] += 1
            continue
        if any(
            decision.get(key) != frozen_row.get(key)
            for key in (
                "observation_key",
                "frame",
                "tracklet_id",
                "source_artifact_digest",
            )
        ):
            action_counts["conflicts"] += 1
            continue
        row_verification = verification_by_subject.get(
            str(decision.get("candidate_subject_id") or ""),
            _empty_ranking_verification(),
        )
        ranked_ids = [
            str(row.get("player_id") or "")
            for row in frozen_row.get("suggestions") or []
        ]
        truth_id = str(decision.get("player_id") or "")
        truth_rank = (
            ranked_ids.index(truth_id) + 1
            if truth_id in ranked_ids
            else None
        )
        new_rows.append(
            {
                "candidate_subject_id": decision.get(
                    "candidate_subject_id"
                ),
                "observation_key": decision.get("observation_key"),
                "ground_truth_player_id": truth_id,
                "ground_truth_team": decision.get("team_label"),
                "ranked_candidate_player_ids": ranked_ids,
                "truth_rank": truth_rank,
                "top1_correct": truth_rank == 1,
                "top3_correct": truth_rank is not None and truth_rank <= 3,
                "abstained": truth_rank is None,
                "cross_team_violations": row_verification[
                    "cross_team_violations"
                ],
                "invalid_ranked_players": row_verification[
                    "invalid_ranked_players"
                ],
                "duplicate_ranked_players": row_verification[
                    "duplicate_ranked_players"
                ],
                "missing_roster_players": row_verification[
                    "missing_roster_players"
                ],
                "ranking_digest": selection["ranking_digest"],
            }
        )
    validation = _load(
        source_root
        / "cross_capture_reid_diagnostic"
        / "cross_capture_reid_validation.json"
    )
    historical = validation.get("preferred_cross_capture_evaluation") or {}
    rows = list(historical.get("rows") or []) + new_rows
    ranks = [
        int(row["truth_rank"])
        for row in rows
        if row.get("truth_rank") is not None
    ]
    queries = len(rows)
    evaluation = {
        "method": "frozen_h1_rankings_to_bounded_h2_operator_ground_truth",
        "queries": queries,
        "historical_queries": len(historical.get("rows") or []),
        "bounded_queries": len(new_rows),
        "top1_accuracy": _ratio(
            sum(bool(row.get("top1_correct")) for row in rows),
            queries,
        ),
        "top3_accuracy": _ratio(
            sum(bool(row.get("top3_correct")) for row in rows),
            queries,
        ),
        "mean_truth_rank": (
            round(sum(ranks) / len(ranks), 4) if ranks else None
        ),
        "median_truth_rank": (
            sorted(ranks)[len(ranks) // 2] if ranks else None
        ),
        "abstentions": sum(bool(row.get("abstained")) for row in rows),
        "cross_team_violations": sum(
            int(row.get("cross_team_violations") or 0) for row in rows
        ),
        "invalid_ranked_players": sum(
            len(row.get("invalid_ranked_players") or []) for row in rows
        ),
        "duplicate_ranked_players": sum(
            len(row.get("duplicate_ranked_players") or []) for row in new_rows
        ),
        "missing_roster_players": sum(
            len(row.get("missing_roster_players") or []) for row in new_rows
        ),
        "action_counts": action_counts,
        "rows": rows,
        "ranking_digest": selection["ranking_digest"],
        "ground_truth_used_as_ranking_input": False,
    }
    internal = validation.get("preferred_internal_calibration") or {}
    runtime_probe = validation.get("runtime_probe") or {}
    gate = build_operator_name_display_gate(
        model_status={
            **(runtime_probe.get("model") or {}),
            "available": (
                runtime_probe.get("status")
                == "PREFERRED_REID_RUNTIME_AVAILABLE"
            ),
            "quality_tier": "preferred_reid_model",
        },
        internal_calibration=internal,
        cross_capture_evaluation=evaluation,
    )
    if queries < int(
        (gate.get("parameters") or {}).get(
            "minimum_cross_capture_queries",
            5,
        )
    ):
        status = "INSUFFICIENT_CROSS_CAPTURE_GROUND_TRUTH"
    elif gate.get("display_eligible"):
        status = "READY_FOR_IA7A_CONTRACT_IMPLEMENTATION"
    else:
        status = "CROSS_CAPTURE_REID_QUALITY_GATE_FAILED"
    result = {
        "schema_version": SCHEMA_VERSION,
        "mode": "bounded_h2_reid_followup_evaluation",
        "status": status,
        "operator_name_status": (
            "OPERATOR_NAMES_ELIGIBLE_FOR_FUTURE_FLOW"
            if gate.get("display_eligible")
            else "OPERATOR_NAMES_REMAIN_HIDDEN"
        ),
        "internal_calibration": internal,
        "cross_capture_evaluation": evaluation,
        "gate": gate,
        "safety": _safety(),
    }
    _write(session_path / "bounded_h2_evaluation.json", result)
    return result


def verify_frozen_bounded_h2_rankings(
    selection: dict[str, Any],
    frozen_rankings: dict[str, Any],
) -> dict[str, Any]:
    """Independently validate every frozen bounded-H2 ranking against roster."""

    roster = {
        str(player.get("player_id") or ""): str(team.get("team_label") or "")
        for team in selection.get("roster") or []
        if isinstance(team, dict)
        for player in team.get("players") or []
        if isinstance(player, dict) and player.get("player_id")
    }
    rows = []
    for ranking in frozen_rankings.get("rankings") or []:
        if not isinstance(ranking, dict):
            continue
        team_label = str(ranking.get("team_label") or "")
        seen: set[str] = set()
        cross_team = 0
        invalid: list[str] = []
        duplicates: list[str] = []
        missing: list[str] = []
        for suggestion in ranking.get("suggestions") or []:
            player_id = str((suggestion or {}).get("player_id") or "")
            if not player_id:
                invalid.append("<empty>")
                continue
            if player_id in seen:
                duplicates.append(player_id)
                invalid.append(player_id)
                continue
            seen.add(player_id)
            player_team = roster.get(player_id)
            if player_team is None:
                missing.append(player_id)
                invalid.append(player_id)
            elif player_team != team_label:
                cross_team += 1
                invalid.append(player_id)
        rows.append(
            {
                "candidate_subject_id": ranking.get("candidate_subject_id"),
                "team_label": team_label,
                "cross_team_violations": cross_team,
                "invalid_ranked_players": sorted(set(invalid)),
                "duplicate_ranked_players": sorted(set(duplicates)),
                "missing_roster_players": sorted(set(missing)),
                "ranking_is_valid": not (
                    cross_team or invalid or duplicates or missing
                ),
            }
        )
    totals = {
        "cross_team_violations": sum(
            int(row["cross_team_violations"]) for row in rows
        ),
        "invalid_ranked_players": sum(
            len(row["invalid_ranked_players"]) for row in rows
        ),
        "duplicate_ranked_players": sum(
            len(row["duplicate_ranked_players"]) for row in rows
        ),
        "missing_roster_players": sum(
            len(row["missing_roster_players"]) for row in rows
        ),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "bounded_h2_frozen_ranking_verification",
        "selection_digest": selection.get("selection_digest"),
        "ranking_digest": canonical_digest(frozen_rankings),
        "rankings_checked": len(rows),
        "rows": rows,
        "totals": totals,
        "historical_result_independently_confirmed": not any(
            totals.values()
        ),
        "safety": _safety(),
    }


def _empty_ranking_verification() -> dict[str, Any]:
    return {
        "cross_team_violations": 0,
        "invalid_ranked_players": [],
        "duplicate_ranked_players": [],
        "missing_roster_players": [],
    }


def _select_temporally_diverse(
    eligible: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected = []
    per_frame: dict[int, int] = {}
    for row in sorted(eligible, key=lambda item: item["selection_score"]):
        frame = int(row["observation"]["frame"])
        if per_frame.get(frame, 0) >= 3:
            continue
        selected.append(row)
        per_frame[frame] = per_frame.get(frame, 0) + 1
        if len(selected) == MAXIMUM_CARDS:
            break
    return selected


def _safety() -> dict[str, Any]:
    return {
        "reran_yolo": False,
        "reran_tracking": False,
        "automatic_identity_assignments": 0,
        "production_applies": 0,
        "source_v4_mutations": 0,
        "portable_operator_visible_suggestions": 0,
        "fabricated_operator_decisions": 0,
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
