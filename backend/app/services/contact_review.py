from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTACT_REVIEW_STATUSES = {"needs_review", "accepted", "rejected", "uncertain"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contact_candidates_path(match_path: Path) -> Path:
    return match_path / "contact_candidates.json"


def _normalize_review_status(value: Any) -> str:
    status = str(value or "needs_review")
    if status not in CONTACT_REVIEW_STATUSES:
        return "needs_review"
    return status


def _update_summary(document: dict[str, Any]) -> None:
    candidates = document.get("candidates") or []
    review_counts = Counter(
        _normalize_review_status(candidate.get("review_status") or candidate.get("status"))
        for candidate in candidates
        if isinstance(candidate, dict)
    )
    players_with_candidates = {
        str(candidate.get("stable_player_id"))
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("stable_player_id")
    }
    interpolated_candidates = sum(
        1
        for candidate in candidates
        if isinstance(candidate, dict) and int(candidate.get("interpolated_player_frames") or 0) > 0
    )
    summary = dict(document.get("summary") or {})
    summary.update(
        {
            "contact_candidates": len(candidates),
            "players_with_candidates": len(players_with_candidates),
            "candidates_with_interpolated_player_positions": interpolated_candidates,
            "review_counts": {status: review_counts.get(status, 0) for status in sorted(CONTACT_REVIEW_STATUSES)},
            "accepted_candidates": review_counts.get("accepted", 0),
            "rejected_candidates": review_counts.get("rejected", 0),
            "uncertain_candidates": review_counts.get("uncertain", 0),
            "needs_review_candidates": review_counts.get("needs_review", 0),
        }
    )
    document["summary"] = summary


def load_contact_candidates_review(match_path: Path) -> dict[str, Any]:
    path = _contact_candidates_path(match_path)
    if not path.exists():
        raise FileNotFoundError("contact_candidates.json not generated yet")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("contact_candidates.json must be a JSON object")
    candidates = document.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("contact_candidates.json must contain candidates list")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        status = _normalize_review_status(candidate.get("review_status") or candidate.get("status"))
        candidate["review_status"] = status
        candidate["status"] = status
        candidate.setdefault("review_notes", "")
    _update_summary(document)
    return document


def save_contact_candidate_reviews(match_path: Path, updates: list[dict[str, Any]]) -> dict[str, Any]:
    document = load_contact_candidates_review(match_path)
    candidates = document.get("candidates") or []
    candidates_by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError("Each contact candidate update must be an object")
        candidate_id = str(update.get("candidate_id") or "")
        if not candidate_id:
            raise ValueError("candidate_id is required")
        candidate = candidates_by_id.get(candidate_id)
        if candidate is None:
            raise ValueError(f"Unknown contact candidate: {candidate_id}")
        status = update.get("review_status", update.get("status", candidate.get("review_status")))
        status = str(status or "needs_review")
        if status not in CONTACT_REVIEW_STATUSES:
            allowed = ", ".join(sorted(CONTACT_REVIEW_STATUSES))
            raise ValueError(f"Invalid review_status '{status}'. Allowed values: {allowed}")
        candidate["review_status"] = status
        candidate["status"] = status
        if "notes" in update:
            candidate["review_notes"] = str(update.get("notes") or "")
        elif "review_notes" in update:
            candidate["review_notes"] = str(update.get("review_notes") or "")
        candidate["reviewed_at"] = _now_iso()

    document["updated_at"] = _now_iso()
    _update_summary(document)
    _contact_candidates_path(match_path).write_text(json.dumps(document, indent=2), encoding="utf-8")
    return document
