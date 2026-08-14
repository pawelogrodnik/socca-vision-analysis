from __future__ import annotations

"""Per-team policy for Reviewed Identity completion and reporting."""

from hashlib import sha256
import json
from typing import Any


IDENTITY_REVIEW_SCOPE_SCHEMA_VERSION = "1.0.0"
COMPLETE_ROSTER = "complete_roster"
TEAM_STATS_ONLY = "team_stats_only"
SUPPORTED_TEAM_SCOPES = frozenset(
    {
        COMPLETE_ROSTER,
        "partial_roster",
        "players_of_interest",
        "unspecified",
        TEAM_STATS_ONLY,
    }
)


def team_review_scope(match_doc: dict[str, Any], team_label: str) -> str:
    """Return configured scope while preserving legacy match semantics."""
    label = _team_label(team_label)
    document = match_doc.get("identity_review_scope") or {}
    configured = str((document.get("teams") or {}).get(label) or "").strip()
    if configured in SUPPORTED_TEAM_SCOPES:
        return configured
    team = next(
        (
            row
            for index, row in enumerate(match_doc.get("teams") or [])
            if _team_label(row.get("team_label") or row.get("label") or chr(ord("A") + index))
            == label
        ),
        {},
    )
    configured = str(team.get("identity_coverage_scope") or "").strip()
    return configured if configured in SUPPORTED_TEAM_SCOPES else "unspecified"


def has_explicit_identity_review_scope(match_doc: dict[str, Any]) -> bool:
    document = match_doc.get("identity_review_scope")
    if isinstance(document, dict):
        teams = document.get("teams")
        if isinstance(teams, dict) and any(
            str(teams.get(label) or "") in SUPPORTED_TEAM_SCOPES
            for label in ("A", "B")
        ):
            return True
    return any(
        str(team.get("identity_coverage_scope") or "") in SUPPORTED_TEAM_SCOPES
        for team in match_doc.get("teams") or []
    )


def identity_review_scope_read_model(match_doc: dict[str, Any]) -> dict[str, Any]:
    explicit = has_explicit_identity_review_scope(match_doc)
    teams = {}
    for label in ("A", "B"):
        scope = team_review_scope(match_doc, label)
        teams[label] = {
            "scope": scope,
            "named_player_review_required": scope != TEAM_STATS_ONLY,
            "team_stats_required": True,
            "player_stats_status": (
                "not_reviewed_by_scope" if scope == TEAM_STATS_ONLY else "reviewed"
            ),
        }
    return {
        "schema_version": IDENTITY_REVIEW_SCOPE_SCHEMA_VERSION,
        "explicit": explicit,
        "teams": teams,
    }


def identity_review_scope_digest(match_doc: dict[str, Any]) -> str:
    semantic = identity_review_scope_read_model(match_doc)
    return sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def review_scope_dependency_matches(
    match_doc: dict[str, Any],
    artifact: dict[str, Any],
) -> bool:
    stored = str(artifact.get("source_review_scope_digest") or "")
    if stored:
        return stored == identity_review_scope_digest(match_doc)
    return not has_explicit_identity_review_scope(match_doc)


def validate_identity_review_scope(document: Any) -> dict[str, Any] | None:
    if document is None:
        return None
    if not isinstance(document, dict):
        raise ValueError("identity_review_scope must be an object")
    teams = document.get("teams")
    if not isinstance(teams, dict):
        raise ValueError("identity_review_scope.teams must be an object")
    normalized = {}
    for label in ("A", "B"):
        scope = str(teams.get(label) or "").strip()
        if scope not in SUPPORTED_TEAM_SCOPES:
            raise ValueError(f"Unsupported identity review scope for Team {label}")
        normalized[label] = scope
    return {
        "schema_version": IDENTITY_REVIEW_SCOPE_SCHEMA_VERSION,
        "teams": normalized,
    }


def _team_label(value: Any) -> str:
    normalized = str(value or "U").upper()
    return normalized if normalized in {"A", "B"} else "U"
