from __future__ import annotations

"""Cheap durable generation fingerprints for Reviewed Identity canonical inputs.

The authoritative snapshot build records ``stat()``-only fingerprints (size in
bytes plus ``mtime_ns``) for every physical file that participates in the
Reviewed Identity semantic digest.  The cheap finalize preflight compares only
these compact values — no JSON parsing and no semantic hashing.

Comparison contract:

- every fingerprint matches          -> canonical generation *maybe* current
- any difference / file added /
  file removed / unknown metadata    -> canonical generation treated stale

A mismatch may cause one extra authoritative recompute; it can never turn a
stale generation into a success.  ``semantic_input_digest`` remains the only
canonical freshness truth.
"""

from pathlib import Path
from typing import Any


FINGERPRINTS_FIELD = "source_file_fingerprints"
FINGERPRINTS_SCHEMA_VERSION = "1.0.0"

# Every physical input of identity_reviewed_snapshot._source_documents(),
# including all files composed by load_combined_operator_seeds() (initial-audit
# seeds + selection, second-half re-anchor seeds + selection) and the match
# document itself.  Keyed by stable logical names; values are repo-relative
# paths inside the match directory.
CANONICAL_SOURCE_FILES: dict[str, str] = {
    "match": "match.json",
    "tracklets": "tracklets.json",
    "subjects": "identity_candidate_shadow.json",
    "timeline": "identity_offline_shadow_timeline.json",
    "operator_seeds": "identity_operator_seeds.json",
    "initial_audit_selection": (
        "identity_initial_audit/identity_initial_audit_frame_selection.json"
    ),
    "reanchor_seeds": (
        "identity_second_half_reanchor/identity_second_half_reanchor_seeds.json"
    ),
    "reanchor_selection": (
        "identity_second_half_reanchor/identity_second_half_reanchor_selection.json"
    ),
    "seeded_assignments": "identity_seeded_candidate_assignments.json",
    "review_decisions": (
        "identity_roster_subject_review_decisions_shadow.json"
    ),
    "slot_review": "reviewed_identity_slot_assignments.json",
    "remediation": "identity_structural_remediation_shadow.json",
    "gallery": "identity_review_gallery.json",
    "stable_players": "stable_players.json",
    "global_identity": "global_identity.json",
    "segment_decisions": "reviewed_identity_segment_decisions.json",
    "material_continuity_decisions": (
        "reviewed_identity_material_continuity_decisions.json"
    ),
    "mixed_players": "reviewed_identity_mixed_players.json",
}


def stat_fingerprint(path: Path) -> dict[str, int] | None:
    """Cheap existence-aware fingerprint; ``None`` means the file is absent."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return {"size_bytes": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def build_canonical_source_fingerprints(match_path: Path) -> dict[str, Any]:
    return {
        "schema_version": FINGERPRINTS_SCHEMA_VERSION,
        "files": {
            name: stat_fingerprint(match_path / relative)
            for name, relative in CANONICAL_SOURCE_FILES.items()
        },
    }


def canonical_generation_maybe_current(
    stored: Any,
    match_path: Path,
) -> bool | None:
    """Compare stored fingerprints against the current filesystem.

    Returns ``True`` when every fingerprint matches, ``False`` on any
    difference (changed size/mtime, file added or removed), and ``None`` when
    no comparable metadata exists (reports written before this field was
    introduced).  ``None`` must be handled as stale by callers.
    """
    if not isinstance(stored, dict):
        return None
    if stored.get("schema_version") != FINGERPRINTS_SCHEMA_VERSION:
        return None
    files = stored.get("files")
    if not isinstance(files, dict):
        return None
    if set(files) != set(CANONICAL_SOURCE_FILES):
        return None
    for name, relative in CANONICAL_SOURCE_FILES.items():
        if stat_fingerprint(match_path / relative) != files.get(name):
            return False
    return True
