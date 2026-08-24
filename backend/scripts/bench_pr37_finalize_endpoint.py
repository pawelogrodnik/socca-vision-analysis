from __future__ import annotations

"""Real end-to-end finalize benchmark for PR #37.

Invokes the EXACT production entry point ``finalize_review_for_qa`` and the
EXACT FastAPI endpoint POST /api/matches/{id}/review-workflow/finalize, so
``performance.total_ms`` covers cheap preflight + authoritative recompute +
fresh progress + hot state + stats + render submission — one operator click.

Benchmark copy: ``bench-finalize-endpoint`` (real match 23391dfb, ~21MB
video).  The durable progress artifact in older copies predates the current
progress schema, so the script first runs the production
``retry_review_recompute`` path until cheap preflight admits finalize — the
same recovery an operator gets in the UI.

Warm vs cold video digest: the durable SHA cache is deleted between runs to
force a full source-video hash on the finalize click path.
"""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.WARNING)

from app.services.identity_reviewed_action_gate import (
    DeferredReviewActionError,
    validate_deferred_review_action,
)
from app.services.identity_reviewed_corrections import persist_reviewed_identity_correction
from app.services.identity_reviewed_hot_state import (
    load_existing_fresh_hot_state,
    rebuild_review_hot_state,
)
from app.services.review_workflow_orchestrator import (
    finalize_review_for_qa,
    refresh_review_after_identity_mutation,
    retry_review_recompute,
)
from app.services.review_workflow_state import build_cheap_finalize_preflight_state


MATCH_ID = "bench-finalize-endpoint"
ROOT = Path("backend/storage/matches") / MATCH_ID
DIGEST_CACHE = "reviewed_source_video_digest.json"

PERFORMANCE_KEYS = (
    "preflight_workflow_ms",
    "seeded_candidate_rebuild_ms",
    "finalize_reviewed_identity_ms",
    "segment_evidence_ms",
    "team_attribution_evidence_ms",
    "progress_build_ms",
    "hot_state_warm_write_ms",
    "stats_ms",
    "render_submit_ms",
    "final_workflow_ms",
    "total_ms",
)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)


def print_performance(performance: dict) -> None:
    for key in PERFORMANCE_KEYS:
        value = performance.get(key)
        print(f"performance.{key}={value}")


def _resolve_semantic_conflicts(match_doc: dict) -> int:
    """Resolve remaining semantic conflict units through the production path.

    Uses the exact operator persistence transaction (deferred gate +
    correction persistence) with action ``unresolved`` — the same save the
    Review UI performs.  No artifacts are hand-forged.
    """
    hot_state = load_existing_fresh_hot_state(ROOT, match_doc) or rebuild_review_hot_state(
        ROOT, match_doc
    )
    progress = dict(hot_state.get("progress") or {})
    targets = [
        case
        for case in progress.get("next_cases") or []
        if case.get("operator_actionable")
        and str(case.get("priority") or "") == "high"
    ]
    resolved = 0
    for case in targets:
        payload = {
            "candidate_subject_id": case.get("candidate_subject_id"),
            "review_target_id": case.get("review_target_id"),
            "action": "unresolved",
            "source_ownership_digest": case.get("source_ownership_digest"),
            "defer_recompute": True,
        }
        try:
            gate = validate_deferred_review_action(ROOT, match_doc, payload)
        except DeferredReviewActionError as exc:
            if str(exc.code) in {"review_unit_already_decided", "review_unit_not_actionable"}:
                continue
            raise
        if gate.get("idempotent_replay"):
            continue
        persist_reviewed_identity_correction(
            ROOT,
            match_doc,
            payload,
            trusted_materialized_detected_team_labels=gate.get(
                "detected_team_labels_by_subject"
            ),
            authorized_review_unit=gate.get("review_unit"),
        )
        resolved += 1
    return resolved


def prepare_eligibility(match_doc: dict) -> str:
    for attempt in range(4):
        state = build_cheap_finalize_preflight_state(ROOT, match_doc)
        if "finalize_identity" in set(state.get("allowed_actions") or []):
            print(f"eligibility_ok attempt={attempt} phase={state['phase']}")
            return state["phase"]
        print(f"preparing attempt={attempt} phase={state['phase']} "
              f"blockers={[b.get('code') for b in state.get('blockers') or []]}")
        blockers = {str(b.get('code')) for b in state.get('blockers') or []}
        if "identity_issues_remaining" in blockers:
            resolved = _resolve_semantic_conflicts(match_doc)
            print(f"resolved_semantic_conflicts={resolved}")
            refresh_review_after_identity_mutation(
                ROOT, match_doc, source="benchmark-prep", leave_hot_state_warm=True,
            )
        elif "render_failed" in blockers or "workflow_busy" in blockers:
            # Bench-copy artifact reset: the previous run's killed encode
            # thread leaves a dead locked job behind.
            ensure_render_not_busy(timeout_sec=5.0)
        elif not blockers:
            # video_qa / complete with current outputs: duplicate finalize is
            # correctly rejected; reset only the bench render artifacts.
            force_finalize_eligible(match_doc)
        else:
            retry_review_recompute(ROOT, match_doc)
    raise RuntimeError("match never became finalize-eligible")


def run_service(label: str, match_doc: dict) -> dict:
    started = time.perf_counter()
    result = finalize_review_for_qa(ROOT, match_doc)
    wall = ms(started)
    performance = result["performance"]
    print(f"\n[{label}] status=200 workflow_phase={result['workflow']['phase']} "
          f"snapshot={str(result['reviewed_identity']['semantic_digest'])[:12]} "
          f"call_wall_ms={wall}")
    print_performance(performance)
    return result


def run_http() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    # NOTE: the URL segment resolves the match DIRECTORY (the benchmark
    # copy), not match.json["id"], exactly like production routing.
    with TestClient(app) as client:
        started = time.perf_counter()
        response = client.post(f"/api/matches/{MATCH_ID}/review-workflow/finalize", json={})
        wall = ms(started)
    body = response.json() if response.status_code == 200 else {}
    performance = body.get("performance") or {}
    print(f"\n[endpoint http] http_status={response.status_code} http_wall_ms={wall} "
          f"bytes={len(response.content)}")
    if performance:
        print_performance(performance)
    else:
        print(f"detail={json.dumps(response.json().get('detail'))[:220]}")


def ensure_render_not_busy(timeout_sec: float = 120.0) -> None:
    """Wait out (or clear) a queued/running render between benchmark runs.

    The submitted render encodes in a background thread of THIS process;
    while it holds the job lock the next finalize click is legitimately
    rejected with ``workflow_busy``.  For benchmark repetition we wait a
    bounded time, then clear the bench-copy job artifacts.
    """
    from app.services.identity_reviewed_output_jobs import (
        LOCK_FILENAME,
        JOB_FILENAME,
        reviewed_output_status_read_only,
    )

    started = time.perf_counter()
    while True:
        try:
            snapshot_digest = load_json(ROOT / "reviewed_identity_report.json").get(
                "snapshot_digest"
            )
        except (OSError, ValueError):
            return
        status = reviewed_output_status_read_only(
            ROOT, snapshot_digest=snapshot_digest
        ).get("status")
        if status == "completed":
            print("render_settled status=completed")
            return
        alive_encode = (
            status in {"queued", "running"}
            and time.perf_counter() - started < timeout_sec
        )
        if alive_encode:
            time.sleep(2.0)
            continue
        print(f"render_settled status={status} -> clearing bench job artifacts")
        (ROOT / JOB_FILENAME).unlink(missing_ok=True)
        (ROOT / LOCK_FILENAME).unlink(missing_ok=True)
        return


def force_finalize_eligible(match_doc: dict) -> None:
    """Reset bench-copy render artifacts so finalize is enterable again.

    After a completed render the workflow correctly sits at ``video_qa``/
    ``complete`` and rejects duplicate finalize (a protected product
    behaviour).  Clearing the render job/lock restores ``ready_to_finalize``
    without touching any canonical Reviewed Identity input.
    """
    from app.services.identity_reviewed_output_jobs import (
        JOB_FILENAME,
        LOCK_FILENAME,
    )

    (ROOT / JOB_FILENAME).unlink(missing_ok=True)
    (ROOT / LOCK_FILENAME).unlink(missing_ok=True)
    state = build_cheap_finalize_preflight_state(ROOT, match_doc)
    if "finalize_identity" not in set(state.get("allowed_actions") or []):
        raise RuntimeError(f"still not eligible: {state['phase']} {state.get('blockers')}")
    print(f"force_eligible_ok phase={state['phase']}")


def main() -> None:
    match_doc = load_json(ROOT / "match.json")
    report = load_json(ROOT / "reviewed_identity_report.json")
    print(f"match={MATCH_ID} pre_run_fingerprints="
          f"{'source_file_fingerprints' in report}")

    prepare_eligibility(match_doc)

    # Exact FastAPI endpoint, warm video digest.
    run_http()

    # Exact production function, warm video digest (full performance dict).
    ensure_render_not_busy()
    force_finalize_eligible(match_doc)
    run_service("service warm video digest", match_doc)
    ensure_render_not_busy()

    # Cold video digest: force full source-video SHA256 inside finalize.
    (ROOT / DIGEST_CACHE).unlink(missing_ok=True)
    ensure_render_not_busy()
    force_finalize_eligible(match_doc)
    run_service("service COLD video digest", match_doc)
    ensure_render_not_busy()

    # Endpoint once more with cold cache for the browser-visible total.
    (ROOT / DIGEST_CACHE).unlink(missing_ok=True)
    ensure_render_not_busy()
    force_finalize_eligible(match_doc)
    run_http()

    refreshed = load_json(ROOT / "reviewed_identity_report.json")
    fingerprints = refreshed.get("source_file_fingerprints") or {}
    print("\npost_run_fingerprints_present=", bool(fingerprints),
          " files=", len(fingerprints.get("files") or {}))


if __name__ == "__main__":
    main()
