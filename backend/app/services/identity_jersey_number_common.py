from __future__ import annotations

import hashlib
import json
import re
from typing import Any


NUMBER_PATTERN = re.compile(r"^[0-9]{1,3}$")
EVIDENCE_STATES = {
    "number_confirmed",
    "number_absent",
    "number_unreadable",
    "number_conflict",
}

CANONICAL_STRUCTURAL_BLOCKERS = frozenset(
    {
        "cross_production_transition",
        "merges_production_subjects",
        "parallel_distant_observation",
        "parallel_roster_candidate_conflict",
        "roster_identity_conflict",
        "structural_identity_conflict",
        "team_switch",
        "temporal_overlap_conflict",
        "uncertain_transition",
        "jersey_number_roster_conflict",
        "cross_team_evidence",
    }
)

STRUCTURAL_BLOCKER_ALIASES = {
    "merges_multiple_production_subjects": "merges_production_subjects",
    "mixed_team_evidence": "cross_team_evidence",
    "parallel_subject_observations": "parallel_distant_observation",
}


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stable_key(kind: str, payload: dict[str, Any]) -> str:
    return f"{kind}:v1:{canonical_digest(payload)}"


def normalize_jersey_number(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or not NUMBER_PATTERN.fullmatch(text):
        return None
    return str(int(text))


def team_label(value: Any) -> str:
    text = str(value or "U").strip().upper()
    return text if text in {"A", "B"} else "U"


def round_or_none(value: Any, digits: int = 4) -> float | None:
    return round(float(value), digits) if isinstance(value, (int, float)) else None


def canonical_structural_blockers(values: Any) -> list[str]:
    normalized = {
        STRUCTURAL_BLOCKER_ALIASES.get(str(value), str(value))
        for value in (values or [])
    }
    return sorted(normalized & CANONICAL_STRUCTURAL_BLOCKERS)


def algorithm_signature(document: dict[str, Any]) -> dict[str, Any] | None:
    algorithm = document.get("algorithm") if isinstance(document, dict) else None
    if not isinstance(algorithm, dict) or not algorithm.get("name") or not algorithm.get("version"):
        return None
    parameters = algorithm.get("parameters") or {}
    return {
        "name": str(algorithm["name"]),
        "version": str(algorithm["version"]),
        "parameters_digest": canonical_digest(parameters),
    }


def lineage_entry(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "digest": canonical_digest(document),
        "algorithm": algorithm_signature(document),
    }


def validate_lineage_entry(
    recorded: dict[str, Any] | None,
    current: dict[str, Any],
    *,
    name: str,
) -> list[str]:
    expected = lineage_entry(current)
    if not isinstance(recorded, dict):
        return [f"{name}_lineage_missing"]
    reasons: list[str] = []
    if recorded.get("digest") != expected["digest"]:
        reasons.append(f"{name}_lineage_mismatch")
    if expected["algorithm"] is None or recorded.get("algorithm") != expected["algorithm"]:
        reasons.append(f"{name}_algorithm_signature_mismatch")
    return reasons
