from __future__ import annotations

"""Small persistent evidence store for human approval of reviewed video QA."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_jersey_number_common import canonical_digest


QA_APPROVAL_FILENAME = "reviewed_video_qa_approval.json"
QA_APPROVAL_SCHEMA_VERSION = "1.0.0"


def load_video_qa_approval(match_path: Path) -> dict[str, Any] | None:
    path = match_path / QA_APPROVAL_FILENAME
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def current_approval_fingerprint(
    snapshot: dict[str, Any] | str,
    stats: dict[str, Any] | None,
    output_job: dict[str, Any] | None,
    output_manifest: dict[str, Any] | None,
) -> dict[str, str | None]:
    """Fingerprints binding a QA approval to exact reviewed artifacts.

    ``snapshot`` may be the full snapshot document or just its semantic
    digest string; compact preflight paths must not load the multi-MB
    snapshot only to read one digest field.
    """
    identity_digest = (
        _text(snapshot.get("semantic_digest"))
        if isinstance(snapshot, dict)
        else _text(snapshot)
    )
    data_generation = (
        output_manifest.get("data_generation")
        if isinstance(output_manifest, dict) and isinstance(output_manifest.get("data_generation"), dict)
        else {}
    )
    visual_generation = (
        output_manifest.get("review_video_generation")
        if isinstance(output_manifest, dict) and isinstance(output_manifest.get("review_video_generation"), dict)
        else {}
    )
    return {
        "reviewed_identity_fingerprint": identity_digest,
        "reviewed_stats_fingerprint": _digest(stats),
        "reviewed_output_fingerprint": _text((output_job or {}).get("video_digest")),
        "reviewed_output_manifest_fingerprint": _digest(output_manifest),
        # A stats-only maintenance generation changes stats and the data part
        # of the output manifest, but not what an operator actually approved
        # visually. This explicit marker lets approval_is_current retain the
        # existing visual QA only when the identity and video digest still
        # match exactly.
        "reviewed_output_data_maintenance": (
            "stats_only" if data_generation.get("maintenance") == "stats_only" else "standard"
        ),
        # Pre-maintenance manifests have no separate generation marker. Their
        # normal workflow gate still verifies the exact identity/scope; only a
        # stats-only refresh writes this marker, and then it is authoritative.
        "reviewed_visual_generation_status": _text(visual_generation.get("status")) or "current",
    }


def approval_is_current(
    approval: dict[str, Any] | None,
    fingerprints: dict[str, str | None],
) -> bool:
    if not approval:
        return False
    if fingerprints.get("reviewed_output_data_maintenance") == "stats_only":
        return bool(
            fingerprints.get("reviewed_visual_generation_status") == "current"
            and
            fingerprints.get("reviewed_identity_fingerprint")
            and fingerprints.get("reviewed_output_fingerprint")
            and approval.get("reviewed_identity_fingerprint") == fingerprints.get("reviewed_identity_fingerprint")
            and approval.get("reviewed_output_fingerprint") == fingerprints.get("reviewed_output_fingerprint")
        )
    return all(
        fingerprints.get(key)
        and approval.get(key) == fingerprints.get(key)
        for key in fingerprints
        if key not in {"reviewed_output_data_maintenance", "reviewed_visual_generation_status"}
    )


def save_video_qa_approval(
    match_path: Path,
    *,
    match_id: str,
    fingerprints: dict[str, str | None],
) -> dict[str, Any]:
    if not all(fingerprints.values()):
        raise ValueError("Current reviewed identity, stats, and video are required for QA approval")
    document = {
        "schema_version": QA_APPROVAL_SCHEMA_VERSION,
        "match_id": match_id,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "source": "operator_video_qa",
        **fingerprints,
    }
    write_identity_json_atomic(match_path / QA_APPROVAL_FILENAME, document)
    return document


def load_json_object(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _digest(document: dict[str, Any] | None) -> str | None:
    return canonical_digest(document) if document else None


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
