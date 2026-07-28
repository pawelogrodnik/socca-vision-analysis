from __future__ import annotations

from typing import Any

from app.services.identity_jersey_number_common import canonical_digest


DIGEST_CONTRACT = "identity_operator_seed_decisions:v1"
NON_SEMANTIC_DECISION_FIELDS = frozenset({"display_order", "updated_at"})


def identity_operator_seed_decisions_digest(
    document: dict[str, Any],
) -> str:
    """Hash identity decisions without mutable UI telemetry or timestamps."""
    source = document.get("source") or {}
    decisions = [
        {
            key: value
            for key, value in row.items()
            if key not in NON_SEMANTIC_DECISION_FIELDS
        }
        for row in document.get("decisions") or []
        if isinstance(row, dict)
    ]
    decisions.sort(
        key=lambda row: (
            str(row.get("observation_key") or ""),
            int(row.get("frame_number") or 0),
            str(row.get("action") or ""),
        )
    )
    return canonical_digest(
        {
            "digest_contract": DIGEST_CONTRACT,
            "schema_version": document.get("schema_version"),
            "mode": document.get("mode"),
            "source": {
                "analysis_run_id": source.get("analysis_run_id"),
                "selection_digest": source.get("selection_digest"),
                "selection_artifact_digest": source.get(
                    "selection_artifact_digest"
                ),
            },
            "decisions": decisions,
        }
    )
