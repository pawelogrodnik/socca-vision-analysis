from __future__ import annotations

"""Operator-only, deterministic projection of interesting action candidates.

The projection deliberately stays outside the public report.  It is cheap to
rebuild from already analysed source artifacts and is keyed by the logical
match generation plus the derived signals, so a review decision can only be
reused for the exact candidate generation that produced it.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app import config
from app.services.artifact_lineage import canonical_json_sha256
from app.services.interesting_action_candidates import (
    POLICY_VERSION,
    build_interesting_action_candidates,
    build_logical_candidate_signals,
)
from app.services.match_groups import get_match_group
from app.services.merged_public_match import group_id_for_merged_published_id


SUGGESTION_SCHEMA_VERSION = "suggested-key-moments.v1"
MATCH_GROUPS_DIR = config.STORAGE_DIR / "published" / "match-groups"
MATCHES_DIR = config.STORAGE_DIR / "matches"
_ARTIFACT_NAMES = {
    "possession_segments": ("possession_segments.json", "segments"),
    "possession_frames": ("possession_candidates.json", "frames"),
    "pass_candidates": ("pass_candidates.json", "candidates"),
    "restart_candidates": ("restart_candidates.json", "candidates"),
    "contact_events": ("event_candidates.json", "events"),
    "momentum_points": ("attacking_momentum.json", "points"),
    "match_phase_periods": ("match_phase_config.json", "periods"),
}


def suggested_key_moment_projection(published_id: str) -> dict[str, Any]:
    """Ensure and return candidates for a merged publication.

    Physical reports retain the manual editor but do not have a logical match
    candidate projection yet.  Missing optional source artifacts result in an
    empty deterministic set instead of triggering any analysis work.
    """

    group_id = group_id_for_merged_published_id(published_id)
    if not group_id:
        return _unavailable("not_a_merged_match")
    manifest = get_match_group(group_id)
    sources = [_source_row(member) for member in _members(manifest)]
    logical = build_logical_candidate_signals(sources)
    span = _number(_record(manifest.get("timing")).get("timeline_span_sec"))
    candidates = build_interesting_action_candidates(logical, timeline_span_sec=span)
    lineage_digest = canonical_json_sha256({
        "schema_version": SUGGESTION_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "group_id": group_id,
        "manifest_digest": str(manifest.get("aggregate_semantic_digest") or ""),
        "timeline_span_sec": span,
        "logical_signals": logical,
    })
    projection = {
        "schema_version": SUGGESTION_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "published_id": published_id,
        "group_id": group_id,
        "candidate_generation_digest": lineage_digest,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }
    _write_if_changed(MATCH_GROUPS_DIR / group_id / "suggested_key_moments.json", projection)
    return {"status": "ready", **projection}


def _source_row(member: Mapping[str, Any]) -> dict[str, Any]:
    match_id = str(member.get("source_match_id") or "")
    match_dir = MATCHES_DIR / match_id
    row: dict[str, Any] = {
        "source_match_id": match_id,
        "logical_offset_sec": _number(member.get("logical_start_sec")),
    }
    for key, (filename, collection) in _ARTIFACT_NAMES.items():
        row[key] = _rows(_read_object(match_dir / filename).get(collection))
    return row


def _write_if_changed(path: Path, value: Mapping[str, Any]) -> None:
    existing = _read_object(path)
    if {key: existing.get(key) for key in value} == dict(value):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {**value, "generated_at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "not_available", "reason": reason, "candidate_count": 0, "candidates": []}


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _members(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [row for row in manifest.get("members", []) if isinstance(row, dict)]


def _rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
