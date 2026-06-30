from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.pass_candidates import (
    PASS_REVIEW_STATUSES,
    build_pass_review_report,
    is_final_stat_pass_candidate,
    normalize_pass_candidate_review_fields,
    normalize_pass_review_status,
    update_pass_candidate_summary,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pass_candidates_path(match_path: Path) -> Path:
    return match_path / "pass_candidates.json"


def load_pass_candidates_review(match_path: Path) -> dict[str, Any]:
    path = _pass_candidates_path(match_path)
    if not path.exists():
        raise FileNotFoundError("pass_candidates.json not generated yet")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("pass_candidates.json must be a JSON object")
    candidates = document.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("pass_candidates.json must contain candidates list")
    for candidate in candidates:
        if isinstance(candidate, dict):
            normalize_pass_candidate_review_fields(candidate)
    update_pass_candidate_summary(document)
    return document


def save_pass_candidate_reviews(match_path: Path, updates: list[dict[str, Any]]) -> dict[str, Any]:
    document = load_pass_candidates_review(match_path)
    candidates = document.get("candidates") or []
    candidates_by_id = {
        str(candidate.get("candidate_id")): candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    for update in updates:
        if not isinstance(update, dict):
            raise ValueError("Each pass candidate update must be an object")
        candidate_id = str(update.get("candidate_id") or "")
        if not candidate_id:
            raise ValueError("candidate_id is required")
        candidate = candidates_by_id.get(candidate_id)
        if candidate is None:
            raise ValueError(f"Unknown pass candidate: {candidate_id}")
        status = normalize_pass_review_status(update.get("review_status", update.get("status", candidate.get("review_status"))))
        if status not in PASS_REVIEW_STATUSES:
            allowed = ", ".join(sorted(PASS_REVIEW_STATUSES))
            raise ValueError(f"Invalid review_status '{status}'. Allowed values: {allowed}")
        candidate["review_status"] = status
        candidate["review_source"] = "manual"
        if "notes" in update:
            candidate["review_notes"] = str(update.get("notes") or "")
        elif "review_notes" in update:
            candidate["review_notes"] = str(update.get("review_notes") or "")
        candidate["reviewed_at"] = _now_iso()
        candidate["final_stat_eligible"] = is_final_stat_pass_candidate(candidate)

    document["updated_at"] = _now_iso()
    update_pass_candidate_summary(document)
    path = _pass_candidates_path(match_path)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    (match_path / "pass_review_report.json").write_text(
        json.dumps(build_pass_review_report(document), indent=2),
        encoding="utf-8",
    )
    return document
