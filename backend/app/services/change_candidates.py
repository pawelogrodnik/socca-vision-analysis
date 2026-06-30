from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CHANGE_SOURCE = "stable_slots_to_change_candidates_v1"
CHANGE_REVIEW_STATUSES = {"needs_review", "confirmed", "rejected", "uncertain", "ignored"}
DEFAULT_MIN_ENTRY_TIME_SEC = 30.0
DEFAULT_MIN_OFF_ON_GAP_SEC = 8.0
DEFAULT_MAX_RETURN_GAP_SEC = 20.0 * 60.0
DEFAULT_REID_CANDIDATES = 5


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_change_candidates_document(
    stable_players_doc: dict[str, Any],
    identity_assignments_doc: dict[str, Any] | None = None,
    *,
    min_entry_time_sec: float = DEFAULT_MIN_ENTRY_TIME_SEC,
    min_off_on_gap_sec: float = DEFAULT_MIN_OFF_ON_GAP_SEC,
    max_return_gap_sec: float = DEFAULT_MAX_RETURN_GAP_SEC,
    reid_candidates_limit: int = DEFAULT_REID_CANDIDATES,
) -> dict[str, Any]:
    players = _stable_players(stable_players_doc)
    assignments = _identity_assignments_by_subject(identity_assignments_doc or {})
    candidates: list[dict[str, Any]] = []
    skipped_reasons: Counter[str] = Counter()

    for incoming in sorted(players, key=lambda item: (_number(item.get("start_time_sec")), _slot_label(item))):
        incoming_start = _number(incoming.get("start_time_sec"))
        if incoming_start < min_entry_time_sec:
            skipped_reasons["initial_or_warmup_slot"] += 1
            continue
        team_key = _team_key(incoming)
        if not team_key:
            skipped_reasons["unknown_team"] += 1
            continue
        previous_same_team = [
            player
            for player in players
            if player is not incoming
            and _team_key(player) == team_key
            and _number(player.get("start_time_sec")) < incoming_start
        ]
        if len(previous_same_team) < _players_per_team(stable_players_doc):
            skipped_reasons["team_not_at_full_known_roster_yet"] += 1
            continue

        out_candidates = _out_candidates(
            incoming,
            previous_same_team,
            min_off_on_gap_sec=min_off_on_gap_sec,
            max_return_gap_sec=max_return_gap_sec,
        )
        if not out_candidates:
            skipped_reasons["no_prior_off_candidate"] += 1
            continue

        reid_candidates = _reid_candidates(
            incoming,
            previous_same_team,
            assignments,
            max_return_gap_sec=max_return_gap_sec,
            limit=reid_candidates_limit,
        )
        primary_out = out_candidates[0]
        top_reid = reid_candidates[0] if reid_candidates else None
        confidence_score = _candidate_confidence(primary_out, top_reid)
        change_id = _change_id(incoming)
        candidates.append(
            {
                "candidate_id": change_id,
                "event_type": "substitution_candidate",
                "source": CHANGE_SOURCE,
                "team_label": incoming.get("team_label"),
                "team_id": incoming.get("team_id"),
                "team_name": incoming.get("team_name"),
                "time_sec": _round((float(primary_out["end_time_sec"]) + incoming_start) / 2.0, 3),
                "gap_sec": primary_out["gap_sec"],
                "confidence": _confidence_label(confidence_score),
                "confidence_score": confidence_score,
                "status": "needs_review",
                "review_status": "needs_review",
                "review_source": "generated",
                "review_notes": "",
                "out_stable_subject_id": primary_out["stable_subject_id"],
                "out_stable_player_id": primary_out["stable_player_id"],
                "out_slot_id": primary_out["slot_id"],
                "out_end_time_sec": primary_out["end_time_sec"],
                "in_stable_subject_id": incoming.get("stable_subject_id"),
                "in_stable_player_id": incoming.get("stable_player_id"),
                "in_slot_id": incoming.get("slot_id"),
                "in_start_time_sec": _round(incoming_start, 3),
                "in_team_confidence": incoming.get("team_confidence"),
                "in_identity_confidence": incoming.get("confidence"),
                "out_candidates": out_candidates,
                "reid_candidates": reid_candidates,
                "suggested_existing_stable_subject_id": top_reid.get("stable_subject_id") if top_reid else None,
                "suggested_real_player_id": top_reid.get("player_id") if top_reid else None,
                "suggested_real_player_name": top_reid.get("player_name") if top_reid else None,
                "notes": [
                    "Candidate only. It marks a possible on/off change, not a final real-player identity link.",
                    "Use review to confirm who went off, who came on, or whether the incoming slot is a returning player.",
                ],
            }
        )

    document = {
        "schema_version": "0.1.0",
        "generated_at": _now_iso(),
        "source": CHANGE_SOURCE,
        "experimental": True,
        "candidate_semantics": "stable_slot_substitution_candidates_not_final_identity_links",
        "parameters": {
            "min_entry_time_sec": min_entry_time_sec,
            "min_off_on_gap_sec": min_off_on_gap_sec,
            "max_return_gap_sec": max_return_gap_sec,
            "reid_candidates_limit": reid_candidates_limit,
            "identity_model": "appearance_color_and_timing_baseline",
        },
        "summary": {},
        "skipped_reasons": dict(skipped_reasons),
        "candidates": candidates,
    }
    update_change_candidate_summary(document)
    return document


def build_change_review_report(change_candidates_doc: dict[str, Any]) -> dict[str, Any]:
    summary = dict(change_candidates_doc.get("summary") or {})
    warnings: list[str] = []
    if int(summary.get("change_candidates") or 0) == 0:
        warnings.append("No substitution/change candidates were generated from stable slots.")
    if int(summary.get("needs_review_candidates") or 0) > 0:
        warnings.append("Some substitution candidates still need review before real-player stint links can use them.")
    return {
        "schema_version": "0.1.0",
        "generated_at": _now_iso(),
        "source": CHANGE_SOURCE,
        "experimental": True,
        "summary": summary,
        "warnings": warnings,
        "notes": [
            "This layer is intentionally conservative.",
            "Confirmed candidates do not automatically rewrite player_identity_assignments yet.",
        ],
    }


def build_change_candidate_artifacts(
    stable_players_doc: dict[str, Any],
    identity_assignments_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    change_candidates = build_change_candidates_document(stable_players_doc, identity_assignments_doc)
    change_review_report = build_change_review_report(change_candidates)
    return {
        "change_candidates": change_candidates,
        "change_review_report": change_review_report,
        "artifacts": {
            "change_candidates": "change_candidates.json",
            "change_review_report": "change_review_report.json",
        },
    }


def write_change_candidate_artifacts(
    match_path: Path,
    stable_players_doc: dict[str, Any] | None = None,
    identity_assignments_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stable_doc = stable_players_doc or _load_json(match_path / "stable_players.json")
    identity_doc = identity_assignments_doc
    if identity_doc is None and (match_path / "player_identity_assignments.json").exists():
        identity_doc = _load_json(match_path / "player_identity_assignments.json")
    existing_reviews = _load_existing_reviews(match_path)
    artifacts = build_change_candidate_artifacts(stable_doc, identity_doc)
    _apply_existing_reviews(artifacts["change_candidates"], existing_reviews)
    artifacts["change_review_report"] = build_change_review_report(artifacts["change_candidates"])
    (match_path / "change_candidates.json").write_text(
        json.dumps(artifacts["change_candidates"], indent=2),
        encoding="utf-8",
    )
    (match_path / "change_review_report.json").write_text(
        json.dumps(artifacts["change_review_report"], indent=2),
        encoding="utf-8",
    )
    return artifacts


def load_change_candidates_review(match_path: Path) -> dict[str, Any]:
    path = match_path / "change_candidates.json"
    if not path.exists():
        if not (match_path / "stable_players.json").exists():
            raise FileNotFoundError("change_candidates.json not generated yet and stable_players.json is missing")
        return write_change_candidate_artifacts(match_path)["change_candidates"]
    document = _load_json(path)
    candidates = document.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("change_candidates.json must contain candidates list")
    for candidate in candidates:
        if isinstance(candidate, dict):
            normalize_change_candidate_review_fields(candidate)
    update_change_candidate_summary(document)
    return document


def save_change_candidate_reviews(match_path: Path, updates: list[dict[str, Any]]) -> dict[str, Any]:
    document = load_change_candidates_review(match_path)
    candidates = document.get("candidates") or []
    by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError("Each change candidate update must be an object")
        candidate_id = str(update.get("candidate_id") or "")
        if not candidate_id:
            raise ValueError("candidate_id is required")
        candidate = by_id.get(candidate_id)
        if candidate is None:
            raise ValueError(f"Unknown change candidate: {candidate_id}")
        status = normalize_change_review_status(update.get("review_status", candidate.get("review_status")))
        if status not in CHANGE_REVIEW_STATUSES:
            allowed = ", ".join(sorted(CHANGE_REVIEW_STATUSES))
            raise ValueError(f"Invalid review_status '{status}'. Allowed values: {allowed}")
        candidate["review_status"] = status
        candidate["status"] = status
        candidate["review_source"] = "manual"
        if "out_stable_subject_id" in update:
            candidate["reviewed_out_stable_subject_id"] = _optional_text(update.get("out_stable_subject_id"))
        if "linked_existing_stable_subject_id" in update:
            candidate["linked_existing_stable_subject_id"] = _optional_text(update.get("linked_existing_stable_subject_id"))
        if "player_id" in update:
            candidate["reviewed_player_id"] = _optional_text(update.get("player_id"))
        if "notes" in update:
            candidate["review_notes"] = str(update.get("notes") or "")
        elif "review_notes" in update:
            candidate["review_notes"] = str(update.get("review_notes") or "")
        candidate["reviewed_at"] = _now_iso()

    document["updated_at"] = _now_iso()
    update_change_candidate_summary(document)
    (match_path / "change_candidates.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
    (match_path / "change_review_report.json").write_text(
        json.dumps(build_change_review_report(document), indent=2),
        encoding="utf-8",
    )
    return document


def normalize_change_candidate_review_fields(candidate: dict[str, Any]) -> None:
    status = normalize_change_review_status(candidate.get("review_status") or candidate.get("status"))
    candidate["review_status"] = status
    candidate["status"] = status
    candidate.setdefault("review_source", "generated")
    candidate.setdefault("review_notes", "")


def normalize_change_review_status(value: Any) -> str:
    status = str(value or "needs_review")
    return status if status in CHANGE_REVIEW_STATUSES else "needs_review"


def update_change_candidate_summary(document: dict[str, Any]) -> None:
    candidates = [item for item in document.get("candidates") or [] if isinstance(item, dict)]
    for candidate in candidates:
        normalize_change_candidate_review_fields(candidate)
    review_counts = Counter(str(candidate.get("review_status") or "needs_review") for candidate in candidates)
    team_counts = Counter(str(candidate.get("team_label") or "unknown") for candidate in candidates)
    summary = dict(document.get("summary") or {})
    summary.update(
        {
            "change_candidates": len(candidates),
            "teams_with_candidates": len(team_counts),
            "candidates_by_team": dict(sorted(team_counts.items())),
            "review_counts": {status: review_counts.get(status, 0) for status in sorted(CHANGE_REVIEW_STATUSES)},
            "needs_review_candidates": review_counts.get("needs_review", 0),
            "confirmed_candidates": review_counts.get("confirmed", 0),
            "uncertain_candidates": review_counts.get("uncertain", 0),
            "rejected_candidates": review_counts.get("rejected", 0),
            "ignored_candidates": review_counts.get("ignored", 0),
        }
    )
    document["summary"] = summary


def _stable_players(stable_players_doc: dict[str, Any]) -> list[dict[str, Any]]:
    raw_players = stable_players_doc.get("players") or stable_players_doc.get("slots") or []
    players = [dict(player) for player in raw_players if isinstance(player, dict)]
    return [
        player
        for player in players
        if player.get("stable_subject_id")
        and player.get("stable_player_id")
        and player.get("start_time_sec") is not None
        and player.get("end_time_sec") is not None
    ]


def _out_candidates(
    incoming: dict[str, Any],
    previous_same_team: list[dict[str, Any]],
    *,
    min_off_on_gap_sec: float,
    max_return_gap_sec: float,
) -> list[dict[str, Any]]:
    incoming_start = _number(incoming.get("start_time_sec"))
    candidates = []
    for player in previous_same_team:
        gap = incoming_start - _number(player.get("end_time_sec"))
        if gap < min_off_on_gap_sec or gap > max_return_gap_sec:
            continue
        score = _out_score(player, incoming, gap, max_return_gap_sec)
        candidates.append(_candidate_player_payload(player) | {"gap_sec": _round(gap, 3), "score": score})
    return sorted(candidates, key=lambda item: (-float(item.get("score") or 0.0), float(item.get("gap_sec") or math.inf)))[:5]


def _reid_candidates(
    incoming: dict[str, Any],
    previous_same_team: list[dict[str, Any]],
    assignments: dict[str, dict[str, Any]],
    *,
    max_return_gap_sec: float,
    limit: int,
) -> list[dict[str, Any]]:
    incoming_start = _number(incoming.get("start_time_sec"))
    candidates = []
    for player in previous_same_team:
        gap = incoming_start - _number(player.get("end_time_sec"))
        if gap < 0 or gap > max_return_gap_sec:
            continue
        color_similarity = _color_similarity(player.get("jersey_color_hex"), incoming.get("jersey_color_hex"))
        timing_score = max(0.0, 1.0 - min(gap, max_return_gap_sec) / max_return_gap_sec)
        score = _round((0.62 * color_similarity) + (0.38 * timing_score), 4)
        payload = _candidate_player_payload(player) | {
            "gap_sec": _round(gap, 3),
            "color_similarity": _round(color_similarity, 4),
            "score": score,
        }
        assignment = assignments.get(str(player.get("stable_subject_id"))) or assignments.get(str(player.get("stable_player_id")))
        if assignment:
            payload.update(
                {
                    "player_id": assignment.get("player_id"),
                    "player_name": assignment.get("player_name"),
                    "player_number": assignment.get("player_number"),
                    "identity_status": assignment.get("status"),
                }
            )
        candidates.append(payload)
    return sorted(candidates, key=lambda item: (-float(item.get("score") or 0.0), float(item.get("gap_sec") or math.inf)))[:limit]


def _candidate_player_payload(player: dict[str, Any]) -> dict[str, Any]:
    return {
        "stable_subject_id": player.get("stable_subject_id"),
        "stable_player_id": player.get("stable_player_id"),
        "slot_id": player.get("slot_id"),
        "team_label": player.get("team_label"),
        "team_id": player.get("team_id"),
        "team_name": player.get("team_name"),
        "start_time_sec": _round(_number(player.get("start_time_sec")), 3),
        "end_time_sec": _round(_number(player.get("end_time_sec")), 3),
        "duration_sec": _round(_number(player.get("duration_sec")), 3),
        "confidence": player.get("confidence"),
        "confidence_score": player.get("confidence_score"),
        "jersey_color_hex": player.get("jersey_color_hex"),
        "detected_time_sec": _movement_number(player, "detected_time_sec"),
        "missing_time_sec": _movement_number(player, "missing_time_sec"),
    }


def _candidate_confidence(primary_out: dict[str, Any], top_reid: dict[str, Any] | None) -> float:
    out_score = float(primary_out.get("score") or 0.0)
    reid_score = float(top_reid.get("score") or 0.0) if top_reid else 0.0
    return _round(max(out_score, reid_score * 0.9), 4)


def _out_score(player: dict[str, Any], incoming: dict[str, Any], gap: float, max_return_gap_sec: float) -> float:
    color_similarity = _color_similarity(player.get("jersey_color_hex"), incoming.get("jersey_color_hex"))
    timing_score = max(0.0, 1.0 - min(gap, max_return_gap_sec) / max_return_gap_sec)
    quality_score = min(1.0, (_movement_number(player, "detected_time_sec") + _movement_number(incoming, "detected_time_sec")) / 120.0)
    return _round((0.52 * timing_score) + (0.34 * color_similarity) + (0.14 * quality_score), 4)


def _identity_assignments_by_subject(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for assignment in document.get("assignments") or []:
        if not isinstance(assignment, dict):
            continue
        for key in ("stable_subject_id", "stable_player_id", "slot_id"):
            value = assignment.get(key)
            if value:
                result[str(value)] = assignment
    return result


def _players_per_team(stable_players_doc: dict[str, Any]) -> int:
    frame_counts = stable_players_doc.get("frame_detection_counts")
    if isinstance(frame_counts, dict):
        target = frame_counts.get("target_players")
        if isinstance(target, (int, float)) and target > 0:
            return max(1, int(target) // 2)
    summary = stable_players_doc.get("summary") or {}
    target = summary.get("target_active_players") or summary.get("slots_total")
    if isinstance(target, (int, float)) and target > 0:
        return max(1, int(target) // 2)
    return 7


def _team_key(player: dict[str, Any]) -> str:
    return str(player.get("team_id") or player.get("team_label") or "")


def _slot_label(player: dict[str, Any]) -> str:
    return str(player.get("stable_player_id") or player.get("slot_id") or player.get("stable_subject_id") or "")


def _change_id(incoming: dict[str, Any]) -> str:
    raw = str(incoming.get("stable_subject_id") or incoming.get("stable_player_id") or "unknown")
    return f"change-{re.sub(r'[^a-zA-Z0-9]+', '-', raw).strip('-').lower()}"


def _confidence_label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _movement_number(player: dict[str, Any], key: str) -> float:
    movement = player.get("movement_stats")
    if isinstance(movement, dict):
        return _number(movement.get(key))
    return 0.0


def _color_similarity(left: Any, right: Any) -> float:
    left_rgb = _hex_to_rgb(left)
    right_rgb = _hex_to_rgb(right)
    if left_rgb is None or right_rgb is None:
        return 0.0
    distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(left_rgb, right_rgb)))
    return max(0.0, 1.0 - distance / math.sqrt(3 * 255**2))


def _hex_to_rgb(value: Any) -> tuple[int, int, int] | None:
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return None


def _number(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return numeric if math.isfinite(numeric) else 0.0


def _round(value: float, digits: int = 4) -> float:
    return round(float(value), digits)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"{path.name} not found")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return document


def _load_existing_reviews(match_path: Path) -> dict[str, dict[str, Any]]:
    path = match_path / "change_candidates.json"
    if not path.exists():
        return {}
    try:
        document = _load_json(path)
    except (json.JSONDecodeError, ValueError):
        return {}
    return {
        str(candidate.get("candidate_id")): candidate
        for candidate in document.get("candidates") or []
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }


def _apply_existing_reviews(document: dict[str, Any], existing_reviews: dict[str, dict[str, Any]]) -> None:
    for candidate in document.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        existing = existing_reviews.get(str(candidate.get("candidate_id")))
        if not existing:
            continue
        for key in (
            "review_status",
            "status",
            "review_source",
            "review_notes",
            "reviewed_at",
            "reviewed_out_stable_subject_id",
            "linked_existing_stable_subject_id",
            "reviewed_player_id",
        ):
            if key in existing:
                candidate[key] = existing[key]
    update_change_candidate_summary(document)
