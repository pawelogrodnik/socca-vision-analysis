from __future__ import annotations

"""Small durable marker for deferred Reviewed Identity propagation."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from app.services.identity_initial_audit_store import write_identity_json_atomic


FILENAME = "reviewed_identity_recompute_required.json"


def mark_reviewed_identity_recompute_required(
    match_path: Path,
    *,
    semantic_decision_digest: str,
) -> dict[str, Any]:
    existing = load_reviewed_identity_recompute_state(match_path)
    document = {
        "schema_version": "1.0.0",
        "status": "required",
        "first_deferred_at": existing.get("first_deferred_at")
        or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "semantic_decision_digest": semantic_decision_digest,
    }
    write_identity_json_atomic(match_path / FILENAME, document)
    return document


def load_reviewed_identity_recompute_state(match_path: Path) -> dict[str, Any]:
    path = match_path / FILENAME
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def reviewed_identity_recompute_required(match_path: Path) -> bool:
    return load_reviewed_identity_recompute_state(match_path).get("status") == "required"


def clear_reviewed_identity_recompute_required(match_path: Path) -> None:
    (match_path / FILENAME).unlink(missing_ok=True)
