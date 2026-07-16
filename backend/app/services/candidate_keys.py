from __future__ import annotations

import hashlib
from typing import Any

from app.services.artifact_lineage import canonical_json_bytes


def contact_candidate_key(candidate: dict[str, Any]) -> str:
    return _key(
        "contact",
        {
            "start_frame": candidate.get("start_frame"),
            "end_frame": candidate.get("end_frame"),
            "stable_identity": candidate.get("stable_subject_id") or candidate.get("stable_player_id"),
            "team_label": candidate.get("team_label"),
        },
    )


def regular_pass_candidate_key(source_candidate_key: Any, target_candidate_key: Any) -> str:
    return _key(
        "pass",
        {
            "source_candidate_key": source_candidate_key,
            "target_candidate_key": target_candidate_key,
        },
    )


def restart_candidate_key(candidate: dict[str, Any]) -> str:
    return _key(
        "restart",
        {
            "setup_start_frame": candidate.get("setup_start_frame"),
            "release_frame": candidate.get("release_frame"),
            "boundary_line": candidate.get("boundary_line"),
        },
    )


def restart_pass_candidate_key(restart_key: Any) -> str:
    return _key("restart_pass", {"restart_candidate_key": restart_key})


def ensure_contact_candidate_keys(document: dict[str, Any]) -> dict[str, Any]:
    for candidate in document.get("candidates") or []:
        if isinstance(candidate, dict):
            candidate["candidate_key"] = contact_candidate_key(candidate)
    return document


def ensure_restart_candidate_keys(document: dict[str, Any]) -> dict[str, Any]:
    for candidate in document.get("candidates") or []:
        if isinstance(candidate, dict):
            candidate["candidate_key"] = restart_candidate_key(candidate)
    return document


def ensure_pass_candidate_keys(
    document: dict[str, Any] | None,
    contact_candidates_doc: dict[str, Any] | None = None,
    restart_candidates_doc: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if document is None:
        return None
    contact_keys = {
        str(candidate.get("candidate_id")): candidate.get("candidate_key") or contact_candidate_key(candidate)
        for candidate in (contact_candidates_doc or {}).get("candidates") or []
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    restart_keys = {
        str(candidate.get("candidate_id")): candidate.get("candidate_key") or restart_candidate_key(candidate)
        for candidate in (restart_candidates_doc or {}).get("candidates") or []
        if isinstance(candidate, dict) and candidate.get("candidate_id")
    }
    for candidate in document.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("from_restart"):
            restart_id = str(candidate.get("restart_candidate_id") or candidate.get("source_candidate_id") or "")
            restart_key = candidate.get("restart_candidate_key") or restart_keys.get(restart_id)
            if restart_key:
                candidate["restart_candidate_key"] = restart_key
                candidate["candidate_key"] = restart_pass_candidate_key(restart_key)
            continue
        source_key = candidate.get("source_candidate_key") or contact_keys.get(
            str(candidate.get("source_candidate_id") or "")
        )
        target_key = candidate.get("target_candidate_key") or contact_keys.get(
            str(candidate.get("target_candidate_id") or "")
        )
        if source_key and target_key:
            candidate["source_candidate_key"] = source_key
            candidate["target_candidate_key"] = target_key
            candidate["candidate_key"] = regular_pass_candidate_key(source_key, target_key)
    return document


def _key(kind: str, payload: dict[str, Any]) -> str:
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"{kind}:v1:{digest}"
