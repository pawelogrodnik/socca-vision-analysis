from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


SESSION_FILENAME = "benchmark_session.json"
STATES = (
    "CREATING",
    "H1_READY",
    "H1_FINISHED",
    "H1_REBUILT",
    "H2_READY",
    "H2_FINISHED",
    "REPORT_READY",
    "FAILED",
)
LEGAL_TRANSITIONS = {
    "CREATING": {"H1_READY", "FAILED"},
    "H1_READY": {"H1_FINISHED", "FAILED"},
    "H1_FINISHED": {"H1_REBUILT", "FAILED"},
    "H1_REBUILT": {"H2_READY", "FAILED"},
    "H2_READY": {"H2_FINISHED", "FAILED"},
    "H2_FINISHED": {"REPORT_READY", "FAILED"},
    "REPORT_READY": set(),
    "FAILED": set(),
}


class ProductFlowStateError(ValueError):
    code = "INVALID_BENCHMARK_STATE"

    def __init__(
        self,
        *,
        current_state: str,
        requested_state: str,
    ) -> None:
        self.current_state = current_state
        self.requested_state = requested_state
        super().__init__(
            f"Illegal product-flow transition: {current_state} -> "
            f"{requested_state}"
        )


def load_product_flow_session(root: Path) -> dict[str, Any]:
    path = root / SESSION_FILENAME
    if not path.exists():
        raise FileNotFoundError("Product-flow benchmark session is missing")
    document = json.loads(path.read_text(encoding="utf-8"))
    state = str(document.get("state") or "")
    if state not in STATES:
        raise ValueError(f"Invalid product-flow benchmark state: {state}")
    return document


def transition_product_flow_session(
    root: Path,
    requested_state: str,
    *,
    action: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically persist an auditable transition; same-state retries are safe."""

    document = load_product_flow_session(root)
    current_state = str(document["state"])
    if current_state == requested_state:
        return document
    if requested_state not in LEGAL_TRANSITIONS[current_state]:
        raise ProductFlowStateError(
            current_state=current_state,
            requested_state=requested_state,
        )
    timestamp = datetime.now(timezone.utc).isoformat()
    event = {
        "sequence": len(document.get("audit_log") or []) + 1,
        "from_state": current_state,
        "to_state": requested_state,
        "action": action,
        "occurred_at": timestamp,
        "details": details or {},
    }
    document = {
        **document,
        "state": requested_state,
        "status": requested_state,
        "updated_at": timestamp,
        "audit_log": [*(document.get("audit_log") or []), event],
    }
    write_json_atomic(root / SESSION_FILENAME, document)
    return document


def fail_product_flow_session(
    root: Path,
    *,
    action: str,
    error: Exception | str,
) -> dict[str, Any]:
    document = load_product_flow_session(root)
    if document["state"] == "FAILED":
        return document
    if "FAILED" not in LEGAL_TRANSITIONS[str(document["state"])]:
        raise ProductFlowStateError(
            current_state=str(document["state"]),
            requested_state="FAILED",
        )
    return transition_product_flow_session(
        root,
        "FAILED",
        action=action,
        details={
            "error_type": (
                type(error).__name__ if isinstance(error, Exception) else "error"
            ),
            "reason": str(error),
        },
    )


def retry_failed_product_flow_session(
    root: Path,
    *,
    expected_previous_state: str,
    action: str,
) -> dict[str, Any]:
    """Resume only the exact stage whose finalization just failed.

    Operator decisions are already durable before downstream rebuilding starts.
    A rebuild/report bug must therefore be retryable without reopening or
    rewriting the completed audit.
    """

    document = load_product_flow_session(root)
    audit_log = document.get("audit_log") or []
    failed_event = audit_log[-1] if audit_log else {}
    if (
        document["state"] != "FAILED"
        or failed_event.get("to_state") != "FAILED"
        or failed_event.get("from_state") != expected_previous_state
    ):
        raise ProductFlowStateError(
            current_state=str(document["state"]),
            requested_state=expected_previous_state,
        )
    timestamp = datetime.now(timezone.utc).isoformat()
    recovery_event = {
        "sequence": len(audit_log) + 1,
        "from_state": "FAILED",
        "to_state": expected_previous_state,
        "action": action,
        "occurred_at": timestamp,
        "details": {
            "retries_failed_sequence": failed_event.get("sequence"),
            "operator_decisions_reused": True,
        },
    }
    recovered = {
        **document,
        "state": expected_previous_state,
        "status": expected_previous_state,
        "updated_at": timestamp,
        "audit_log": [*audit_log, recovery_event],
    }
    write_json_atomic(root / SESSION_FILENAME, recovered)
    return recovered


def benchmark_context_for_workspace(match_path: Path) -> dict[str, Any] | None:
    resolved_match_path = match_path.resolve()
    session_path = resolved_match_path.parent / SESSION_FILENAME
    if not session_path.exists():
        return None
    document = load_product_flow_session(resolved_match_path.parent)
    match_document = json.loads(
        (resolved_match_path / "match.json").read_text(encoding="utf-8")
    )
    benchmark = match_document.get("benchmark_session") or {}
    if str(benchmark.get("id") or "") != str(document.get("benchmark_id") or ""):
        raise ValueError("Benchmark workspace/session lineage mismatch")
    return {
        "root": resolved_match_path.parent,
        "state": document["state"],
        "domain": str(benchmark.get("domain") or ""),
        "session": document,
    }


def write_json_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
