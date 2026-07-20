from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "0.1.0"
ALGORITHM_NAME = "identity_fragment_visual_content_evidence"
ALGORITHM_VERSION = "0.1.0"

CONTENT_STATUSES = {
    "person",
    "partial_person",
    "not_person",
    "unclear",
    "pending",
    "unavailable",
}
PERSON_SUPPORT_STATUSES = {"person", "partial_person"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_endpoint_key(proposal: dict[str, Any], *, side: str) -> str:
    if side not in {"source", "target"}:
        raise ValueError(f"Unsupported endpoint side: {side}")
    endpoint = proposal.get(f"{side}_endpoint") or {}
    payload = {
        "candidate_subject_id": proposal.get(f"{side}_candidate_subject_id"),
        "frame": endpoint.get("frame"),
        "bbox_xyxy": _normalized_bbox(endpoint.get("bbox_xyxy")),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"identity-endpoint:v1:{digest}"


def build_identity_fragment_visual_content_evidence(
    consolidation_doc: dict[str, Any],
    *,
    reviewed_audits: list[dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Attach explicit endpoint-content evidence without changing identity decisions."""
    labels = _review_index(reviewed_audits or [])
    endpoints: dict[str, dict[str, Any]] = {}
    pairs: list[dict[str, Any]] = []
    for proposal in consolidation_doc.get("proposals") or []:
        pair_evidence: dict[str, Any] = {
            "proposal_key": proposal.get("proposal_key"),
        }
        for side in ("source", "target"):
            endpoint_key = build_endpoint_key(proposal, side=side)
            label = labels.get(endpoint_key)
            endpoint = proposal.get(f"{side}_endpoint") or {}
            status = str((label or {}).get("status") or "unavailable")
            if status not in CONTENT_STATUSES:
                raise ValueError(f"Unsupported endpoint content status: {status}")
            evidence = {
                "endpoint_key": endpoint_key,
                "candidate_subject_id": proposal.get(f"{side}_candidate_subject_id"),
                "candidate_player_id": proposal.get(f"{side}_candidate_player_id"),
                "team_label": proposal.get(f"{side}_team_label"),
                "frame": endpoint.get("frame"),
                "bbox_xyxy": _normalized_bbox(endpoint.get("bbox_xyxy")),
                "status": status,
                "person_content_supported": status in PERSON_SUPPORT_STATUSES,
                "blocks_automatic_identity_merge": status == "not_person",
                "source": str((label or {}).get("source") or "none"),
                "reviewed_at": (label or {}).get("reviewed_at"),
                "notes": str((label or {}).get("notes") or ""),
                "advisory_only": True,
            }
            existing = endpoints.get(endpoint_key)
            if existing is not None and _evidence_signature(existing) != _evidence_signature(evidence):
                raise ValueError(f"Conflicting visual-content evidence for {endpoint_key}")
            endpoints[endpoint_key] = evidence
            pair_evidence[f"{side}_endpoint_key"] = endpoint_key
            pair_evidence[f"{side}_status"] = status
        source_status = str(pair_evidence["source_status"])
        target_status = str(pair_evidence["target_status"])
        pair_evidence.update(_pair_summary(source_status, target_status))
        pairs.append(pair_evidence)

    endpoint_rows = [endpoints[key] for key in sorted(endpoints)]
    status_counts = Counter(str(row["status"]) for row in endpoint_rows)
    pair_counts = Counter(str(row["quality"]) for row in pairs)
    generated = generated_at or now_iso()
    document = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "mode": "shadow_endpoint_visual_content",
        "algorithm": {"name": ALGORITHM_NAME, "version": ALGORITHM_VERSION},
        "source": {
            "consolidation_algorithm": consolidation_doc.get("algorithm") or {},
            "review_documents": len(reviewed_audits or []),
        },
        "summary": {
            "unique_endpoints": len(endpoint_rows),
            "proposal_pairs": len(pairs),
            "endpoint_status_counts": dict(sorted(status_counts.items())),
            "pair_quality_counts": dict(sorted(pair_counts.items())),
            "not_person_endpoints": status_counts["not_person"],
        },
        "safety": {
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
            "safe_for_automatic_identity_merge": False,
        },
        "endpoints": endpoint_rows,
        "pairs": pairs,
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated,
        "algorithm": document["algorithm"],
        "status": "ready" if endpoint_rows else "no_endpoints",
        "summary": document["summary"],
        "gates": {
            "identity_outputs_untouched": True,
            "content_evidence_is_advisory_only": all(row["advisory_only"] for row in endpoint_rows),
            "no_unreviewed_content_used_as_person_evidence": all(
                row["source"] != "none" or not row["person_content_supported"]
                for row in endpoint_rows
            ),
        },
        "limitations": [
            "Manual content labels verify endpoint visibility, not whether two endpoints are the same person.",
            "Missing visual-content evidence remains unavailable and never authorizes a merge.",
            "A learned person verifier is not included in this stage.",
        ],
    }
    return {
        "identity_fragment_visual_content": document,
        "identity_fragment_visual_content_report": report,
    }


def _review_index(audits: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for audit in audits:
        if str(audit.get("audit_kind") or "") != "fragment_endpoint_content":
            raise ValueError("Reviewed manifest is not a fragment endpoint content audit")
        for item in audit.get("items") or []:
            endpoint_key = str(item.get("endpoint_key") or "").strip()
            review = item.get("manual_review") or {}
            status = str(review.get("status") or "pending")
            if not endpoint_key:
                raise ValueError("Visual-content review item is missing endpoint_key")
            if status not in CONTENT_STATUSES:
                raise ValueError(f"Unsupported endpoint content status: {status}")
            row = {
                "status": status,
                "source": "manual_review",
                "reviewed_at": review.get("reviewed_at"),
                "notes": review.get("notes") or "",
            }
            previous = result.get(endpoint_key)
            if previous is not None and _evidence_signature(previous) != _evidence_signature(row):
                raise ValueError(f"Conflicting manual reviews for {endpoint_key}")
            result[endpoint_key] = row
    return result


def _pair_summary(source_status: str, target_status: str) -> dict[str, Any]:
    statuses = {source_status, target_status}
    if "not_person" in statuses:
        quality = "invalid_content"
    elif statuses.issubset(PERSON_SUPPORT_STATUSES):
        quality = "person_content_supported"
    elif "unclear" in statuses:
        quality = "unclear"
    else:
        quality = "unavailable"
    return {
        "quality": quality,
        "person_content_supported": quality == "person_content_supported",
        "blocks_automatic_identity_merge": quality == "invalid_content",
        "safe_for_automatic_identity_merge": False,
        "advisory_only": True,
    }


def _normalized_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 4:
        return None
    return [round(float(component), 3) for component in value[:4]]


def _evidence_signature(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("status"),
        row.get("source"),
        row.get("notes"),
    )
