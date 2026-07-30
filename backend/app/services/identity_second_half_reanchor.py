from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit import (
    build_initial_identity_audit_document,
    export_identity_audit_frames,
    identity_audit_selection_artifacts_exist,
    read_identity_audit_visual_metrics,
)
from app.services.identity_initial_audit_frame_selection import (
    build_initial_identity_audit_frame_selection,
    collect_candidate_frame_numbers,
)
from app.services.identity_initial_audit_store import (
    find_identity_artifact,
    load_identity_json,
    write_identity_json_atomic,
)


SCHEMA_VERSION = "0.1.0"
MODE = "second_half_identity_reanchor"
REANCHOR_DIRECTORY = "identity_second_half_reanchor"
SELECTION_FILENAME = "identity_second_half_reanchor_selection.json"
FRAME_DIRECTORY = f"{REANCHOR_DIRECTORY}/frames"
SEEDED_ASSIGNMENTS_FILENAME = "identity_seeded_candidate_assignments.json"
MINIMUM_SAFE_H2_PLAYERS = 3
MINIMUM_SAFE_H2_FRAMES = 30


def prepare_second_half_identity_reanchor(
    match_path: Path,
    video_path: Path,
    match_document: dict[str, Any],
    *,
    force: bool = False,
) -> dict[str, Any]:
    phase_config = _load_phase_config(match_path)
    second_half_start_sec = _second_half_start_time(phase_config)
    if second_half_start_sec is None:
        return _status_document(
            "not_applicable",
            "second_half_not_configured",
            match_document,
        )

    analysis_report = _load_required_artifact(
        match_path,
        match_document,
        "analysis_report.json",
    )
    fps = max(
        1.0,
        float((analysis_report.get("video") or {}).get("fps") or 30.0),
    )
    second_half_start_frame = max(0, round(second_half_start_sec * fps))
    audit_path = match_path / REANCHOR_DIRECTORY
    selection_path = audit_path / SELECTION_FILENAME
    safely_resolved_players = _safely_resolved_h2_players(
        match_path,
        match_document,
        second_half_start_frame=second_half_start_frame,
    )
    # H2 may become safely covered as a direct result of the first few H2
    # confirmations.  That must not invalidate the operator's open session
    # or turn the next save into a 409.  The auto-skip is only for a session
    # that has not collected any operator decisions yet.
    if (
        len(safely_resolved_players) >= MINIMUM_SAFE_H2_PLAYERS
        and not _has_existing_operator_decisions(audit_path)
    ):
        return _status_document(
            "skipped_already_resolved",
            "first_half_seeds_safely_cover_second_half",
            match_document,
            second_half_start_sec=second_half_start_sec,
            safely_resolved_players=safely_resolved_players,
        )

    selection = None
    if not force and selection_path.exists():
        candidate = load_identity_json(selection_path)
        if identity_audit_selection_artifacts_exist(match_path, candidate):
            selection = candidate

    if selection is None:
        global_identity = _load_required_artifact(
            match_path,
            match_document,
            "global_identity.json",
        )
        tracklets = _load_required_artifact(
            match_path,
            match_document,
            "tracklets.json",
        )
        camera_motion = _load_optional_artifact(
            match_path,
            match_document,
            "camera_motion_report.json",
        )
        candidate_frames = [
            frame
            for frame in collect_candidate_frame_numbers(
                global_identity,
                stride_frames=15,
            )
            if frame >= second_half_start_frame
        ]
        visual_metrics = read_identity_audit_visual_metrics(
            video_path,
            candidate_frames,
        )
        selection = build_initial_identity_audit_frame_selection(
            global_identity,
            tracklets,
            analysis_report,
            camera_motion_report=camera_motion,
            frame_visual_metrics=visual_metrics,
            seeded_subject_ids=set(),
            parameters={
                "target_frame_count": 3,
                "maximum_frame_count": 3,
                "minimum_visible_players": 5,
                "minimum_frame_gap_sec": 8.0,
            },
            generated_at=datetime.now(timezone.utc).isoformat(),
            artifact_directory=FRAME_DIRECTORY,
            minimum_frame=second_half_start_frame,
        )
        selection["mode"] = "second_half_identity_reanchor_selection_shadow"
        selection["second_half"] = {
            "start_time_sec": second_half_start_sec,
            "start_frame": second_half_start_frame,
            "safely_resolved_players_before_reanchor": (
                safely_resolved_players
            ),
        }
        frames = [int(row["frame"]) for row in selection["selected_frames"]]
        frame_path = audit_path / "frames"
        frame_path.mkdir(parents=True, exist_ok=True)
        export_identity_audit_frames(video_path, frames, frame_path)
        write_identity_json_atomic(selection_path, selection)

    snapshot_players = (selection.get("second_half") or {}).get(
        "safely_resolved_players_before_reanchor"
    )
    suggestion_players = (
        snapshot_players
        if isinstance(snapshot_players, list)
        else safely_resolved_players
    )
    return build_second_half_identity_reanchor_document(
        selection,
        match_document,
        safely_resolved_players=suggestion_players,
    )


def build_second_half_identity_reanchor_document(
    selection: dict[str, Any],
    match_document: dict[str, Any],
    *,
    safely_resolved_players: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bounded_selection = {
        **selection,
        "selected_frames": list(selection.get("selected_frames") or [])[:3],
    }
    document = build_initial_identity_audit_document(
        bounded_selection,
        match_document,
    )
    second_half = selection.get("second_half") or {}
    h1_safe_lineage_allowed = (
        second_half.get("h1_safe_lineage_allowed") is not False
    )
    suggestion_rows = (
        (
            safely_resolved_players
            if safely_resolved_players is not None
            else (
                second_half.get(
                    "safely_resolved_players_before_reanchor"
                )
                or []
            )
        )
        if h1_safe_lineage_allowed
        else []
    )
    suggestion_by_tracklet = _suggested_players_by_tracklet(
        suggestion_rows
    )
    roster_team_by_player = {
        str(player.get("player_id") or ""): str(
            team.get("team_label") or "U"
        )
        for team in document.get("roster") or []
        for player in team.get("players") or []
        if player.get("player_id")
    }
    advisory_by_tracklet = _advisory_suggestions_by_tracklet(
        selection.get("reid_advisory_suggestions") or []
    )
    for frame in document.get("frames") or []:
        for observation in frame.get("observations") or []:
            tracklet_id = str(
                (observation.get("provenance") or {}).get("tracklet_id")
                or ""
            )
            safe_suggestion = suggestion_by_tracklet.get(tracklet_id)
            observation_team = str(
                observation.get("team_label") or "U"
            )
            if safe_suggestion is not None and not _teams_compatible(
                observation_team,
                str(safe_suggestion.get("team_label") or "U"),
            ):
                safe_suggestion = None
            observation["suggested_player"] = (
                {
                    **safe_suggestion,
                    "observation_key": observation.get("observation_key"),
                }
                if safe_suggestion is not None
                else None
            )
            advisory = advisory_by_tracklet.get(tracklet_id)
            if advisory:
                compatible_suggestions = [
                    suggestion
                    for suggestion in advisory["suggestions"]
                    if (
                        str(suggestion.get("player_id") or "")
                        in roster_team_by_player
                        and _teams_compatible(
                            observation_team,
                            roster_team_by_player[
                                str(suggestion.get("player_id") or "")
                            ],
                        )
                    )
                ]
                observation["reid_suggestions"] = [
                    {
                        **suggestion,
                        "suggestion_source": (
                            "cross_analysis_reid_top3_advisory"
                        ),
                        "advisory_only": True,
                        "candidate_subject_id": advisory.get(
                            "candidate_subject_id"
                        ),
                        "observation_key": observation.get(
                            "observation_key"
                        ),
                    }
                    for suggestion in compatible_suggestions
                ]
                if (
                    observation["suggested_player"] is None
                    and observation["reid_suggestions"]
                ):
                    observation["suggested_player"] = {
                        **observation["reid_suggestions"][0],
                        "team_label": roster_team_by_player[
                            str(
                                observation["reid_suggestions"][0].get(
                                    "player_id"
                                )
                                or ""
                            )
                        ],
                    }
    document.update(
        {
            "schema_version": SCHEMA_VERSION,
            "mode": MODE,
            "status": "ready",
            "reason": None,
            "second_half": second_half,
            "safely_resolved_players": safely_resolved_players or [],
        }
    )
    document["summary"] = {
        **(document.get("summary") or {}),
        "maximum_frames": 3,
        "target_actions": "3-5 confirmations",
        "confirmation_first": True,
    }
    document["operator_contract"] = {
        **(document.get("operator_contract") or {}),
        "confirmation_first": True,
        "second_full_lineup_audit": False,
    }
    return document


def load_second_half_reanchor_selection(match_path: Path) -> dict[str, Any]:
    path = match_path / REANCHOR_DIRECTORY / SELECTION_FILENAME
    if not path.exists():
        raise FileNotFoundError("Second-half re-anchor selection is missing")
    return load_identity_json(path)


def _has_existing_operator_decisions(audit_path: Path) -> bool:
    seed_path = audit_path / "identity_second_half_reanchor_seeds.json"
    if not seed_path.exists():
        return False
    seed_document = load_identity_json(seed_path)
    return bool(seed_document.get("decisions") or [])


def _safely_resolved_h2_players(
    match_path: Path,
    match_document: dict[str, Any],
    *,
    second_half_start_frame: int,
) -> list[dict[str, Any]]:
    assignments_path = match_path / SEEDED_ASSIGNMENTS_FILENAME
    timeline_path = find_identity_artifact(
        match_path,
        match_document,
        "identity_offline_shadow_timeline.json",
    )
    if not assignments_path.exists() or timeline_path is None:
        return []
    assignments = load_identity_json(assignments_path)
    timeline = load_identity_json(timeline_path)
    timeline_by_subject = {
        str(row.get("shadow_subject_id") or ""): row
        for row in timeline.get("subjects") or []
        if isinstance(row, dict) and row.get("shadow_subject_id")
    }
    resolved: list[dict[str, Any]] = []
    seen_players: set[str] = set()
    for assignment in assignments.get("accepted_assignments") or []:
        player = assignment.get("assigned_player") or {}
        player_id = str(player.get("player_id") or "")
        if not player_id or player_id in seen_players:
            continue
        subject_id = str(assignment.get("candidate_subject_id") or "")
        observations = (
            timeline_by_subject.get(subject_id, {}).get("observations") or []
        )
        trusted_frames = {
            int(row.get("frame") or 0)
            for row in observations
            if int(row.get("frame") or 0) >= second_half_start_frame
            and str(row.get("status") or "detected") == "detected"
            and row.get("visual_trusted") is not False
        }
        if len(trusted_frames) < MINIMUM_SAFE_H2_FRAMES:
            continue
        seen_players.add(player_id)
        resolved.append(
            {
                "player_id": player_id,
                "player_name": player.get("player_name"),
                "team_label": assignment.get("team_label"),
                "candidate_subject_id": subject_id,
                "tracklet_ids": sorted(
                    {
                        str(value)
                        for value in assignment.get("tracklet_ids") or []
                        if value not in (None, "")
                    }
                ),
                "trusted_second_half_frames": len(trusted_frames),
            }
        )
    return sorted(resolved, key=lambda row: str(row.get("player_id") or ""))


def _suggested_players_by_tracklet(
    safely_resolved_players: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in safely_resolved_players:
        for tracklet_id in row.get("tracklet_ids") or []:
            result[str(tracklet_id)] = {
                "player_id": row.get("player_id"),
                "player_name": row.get("player_name"),
                "team_label": row.get("team_label"),
                "candidate_subject_id": row.get("candidate_subject_id"),
                "suggestion_source": "h1_safe_lineage",
                "advisory_only": False,
            }
    return result


def _advisory_suggestions_by_tracklet(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        suggestions = list(row.get("suggestions") or [])[:3]
        if not suggestions:
            continue
        for tracklet_id in row.get("tracklet_ids") or []:
            result[str(tracklet_id)] = {
                "team_label": row.get("team_label"),
                "candidate_subject_id": row.get("candidate_subject_id"),
                "suggestions": suggestions,
            }
    return result


def _teams_compatible(
    observation_team: str,
    player_team: str,
) -> bool:
    return (
        observation_team not in {"A", "B"}
        or player_team not in {"A", "B"}
        or observation_team == player_team
    )


def _second_half_start_time(
    phase_config: dict[str, Any] | None,
) -> float | None:
    if not phase_config:
        return None
    explicit = phase_config.get("second_half_start_time_sec")
    if isinstance(explicit, (int, float)) and float(explicit) >= 0.0:
        return float(explicit)
    for period in phase_config.get("periods") or []:
        period_id = str(period.get("period_id") or "").lower()
        if period_id in {"second_half", "2h", "h2"}:
            value = period.get("start_time_sec")
            if isinstance(value, (int, float)) and float(value) >= 0.0:
                return float(value)
    return None


def _load_phase_config(match_path: Path) -> dict[str, Any] | None:
    path = match_path / "match_phase_config.json"
    return load_identity_json(path) if path.exists() else None


def _load_required_artifact(
    match_path: Path,
    match_document: dict[str, Any],
    filename: str,
) -> dict[str, Any]:
    document = _load_optional_artifact(
        match_path,
        match_document,
        filename,
    )
    if document is None:
        raise FileNotFoundError(f"{filename} not found. Run analysis first.")
    return document


def _load_optional_artifact(
    match_path: Path,
    match_document: dict[str, Any],
    filename: str,
) -> dict[str, Any] | None:
    path = find_identity_artifact(match_path, match_document, filename)
    return load_identity_json(path) if path is not None else None


def _status_document(
    status: str,
    reason: str,
    match_document: dict[str, Any],
    *,
    second_half_start_sec: float | None = None,
    safely_resolved_players: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    roster_document = build_initial_identity_audit_document(
        {"selected_frames": [], "video": {}},
        match_document,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "status": status,
        "reason": reason,
        "second_half": {
            "start_time_sec": second_half_start_sec,
        },
        "safely_resolved_players": safely_resolved_players or [],
        "summary": {
            "selected_frames": 0,
            "visible_observations": 0,
            "maximum_frames": 3,
            "target_actions": "3-5 confirmations",
            "confirmation_first": True,
        },
        "roster": roster_document.get("roster") or [],
        "frames": [],
        "actions": roster_document.get("actions") or [],
        "operator_contract": {
            "confirmation_first": True,
            "second_full_lineup_audit": False,
            "finish_before_full_coverage": True,
        },
        "safety": {
            "production_identity_untouched": True,
            "candidate_identity_untouched": True,
            "yolo_not_required": True,
        },
    }
