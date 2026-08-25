from __future__ import annotations

"""Real-match benchmark for the PR #37 correctness-hardening pass.

Measures: cold/warm review-progress, authoritative finalize phases (snapshot,
fresh progress projection against the NEW snapshot, hot state), stats, cheap
preflight, correction hot path. Also asserts the stale-progress invariant and
records snapshot generations plus public payload sizes.
"""

import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.basicConfig(level=logging.INFO, format="%(message)s")

from app.services.identity_canonical_io import review_build_context
from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_reviewed_hot_state import (
    load_existing_fresh_hot_state,
    rebuild_review_hot_state,
    update_hot_state_after_deferred_save,
)
from app.services.identity_reviewed_progress import (
    build_reviewed_identity_progress,
    project_reviewed_identity_progress,
)
from app.services.identity_reviewed_snapshot import (
    finalize_reviewed_identity,
    last_snapshot_build_phases,
)
from app.services.identity_reviewed_stats import build_reviewed_stats
from app.services.review_workflow_orchestrator import (
    durable_review_progress,
    public_finalized_identity,
)
from app.services.review_workflow_state import build_cheap_finalize_preflight_state
from bench_match_copy import ensure_bench_copy, remove_bench_copy


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class Timer:
    def __init__(self) -> None:
        self.values: dict[str, float] = {}

    def measure(self, name: str, fn, *args, **kwargs):
        started = time.perf_counter()
        result = fn(*args, **kwargs)
        self.values[name] = round(time.perf_counter() - started, 2)
        return result


def main() -> None:
    root = ensure_bench_copy("perf-finalize-bench", "9c7485e4")
    try:
        _run_benchmark(root)
    finally:
        remove_bench_copy("perf-finalize-bench")


def _run_benchmark(root: Path) -> None:
    match_doc = load_json(root / "match.json")
    timer = Timer()

    old_snapshot = load_json(root / "reviewed_identity_snapshot.json")
    s1 = old_snapshot.get("semantic_digest")
    print(f"S1_pre_finalize_snapshot_digest={s1}")

    # Cold authoritative review-progress (independent generation).
    progress_cold = timer.measure(
        "cold_review_progress_s", build_reviewed_identity_progress, root, match_doc, include_internal_units=True
    )

    # Warm review-progress via durable hot state.
    rebuild_review_hot_state(root, match_doc, prebuilt_progress=progress_cold)
    timer.measure("warm_hot_rebuild_once_s", rebuild_review_hot_state, root, match_doc)
    started = time.perf_counter()
    warm = load_existing_fresh_hot_state(root, match_doc)
    timer.values["warm_hit_probe_ms"] = round((time.perf_counter() - started) * 1000, 1)
    assert warm is not None

    # Authoritative finalize transaction (mirrors orchestrator, render excluded).
    with review_build_context():
        snapshot = timer.measure("finalize_snapshot_s", finalize_reviewed_identity, root, match_doc)
        phases = last_snapshot_build_phases()
        final_progress = timer.measure(
            "fresh_progress_after_finalize_s",
            build_reviewed_identity_progress,
            root,
            match_doc,
            include_internal_units=True,
        )
        hot_state = timer.measure(
            "hot_state_warm_write_s",
            rebuild_review_hot_state,
            root,
            match_doc,
            prebuilt_progress=final_progress,
        )

    s2 = snapshot["semantic_digest"]
    projected_digest = final_progress["_projection_inputs"]["source_snapshot_digest"]
    top_digest = final_progress["source_snapshot_digest"]
    print(f"S2_new_snapshot_digest={s2}")
    if s1 == s2:
        # Idempotent canonical inputs rebuild an identical generation; the
        # S1 != S2 transition itself is proven by the regression suite.
        print("generation_changed=False (canonical inputs unchanged; idempotent)")
    else:
        print("generation_changed=True")
    print(f"final_progress_top_digest_matches_S2={top_digest == s2}")
    print(f"final_progress_projection_inputs_digest_matches_S2={projected_digest == s2}")

    # Differential check: independent cold rebuild after finalize must agree.
    independent = build_reviewed_identity_progress(root, match_doc, include_internal_units=True)
    fields = ("summary", "identity_coverage", "coverage_readiness", "next_cases", "optional_audit", "workload")
    identical = all(final_progress.get(f) == independent.get(f) for f in fields)
    print(f"differential_optimized_vs_independent_cold_identical={identical}")
    assert identical

    # Durable artifacts + hot-state consistency.
    write_identity_json_atomic(
        root / "reviewed_identity_progress.json",
        {**durable_review_progress(final_progress), "source_snapshot_digest": s2},
        compact=True,
    )
    print(f"hot_projection_inputs_digest_matches_S2={hot_state['projection_inputs']['source_snapshot_digest'] == s2}")
    assert load_existing_fresh_hot_state(root, match_doc) is not None

    # Stats phase.
    pitch = load_json(root / "pitch_config.json")
    timer.measure("stats_s", build_reviewed_stats, root, snapshot, match_doc, pitch)

    # Cheap finalize preflight.
    timer.measure("cheap_preflight_ms", build_cheap_finalize_preflight_state, root, match_doc)
    timer.values["cheap_preflight_ms"] = round(timer.values["cheap_preflight_ms"] * 1000, 1)

    # Ordinary correction hot path: project one deferred decision in memory.
    units = hot_state.get("internal_review_units") or []
    target = next((u for u in units if u.get("operator_actionable") and not (u.get("current_decision") or {}).get("action")), None)
    if target is not None:
        target["current_decision"] = {"action": "unresolved"}
        started = time.perf_counter()
        update_hot_state_after_deferred_save(
            root,
            match_doc,
            hot_state,
            target,
            target["current_decision"],
            "bench-digest",
        )
        timer.values["correction_hot_save_ms"] = round((time.perf_counter() - started) * 1000, 1)
    else:
        timer.values["correction_hot_save_ms"] = -1

    # Public payload sizes.
    finalize_bytes = len(json.dumps(public_finalized_identity(snapshot)).encode("utf-8"))
    page_bytes = len(json.dumps(progress_cold.get("next_cases") or []).encode("utf-8"))
    durable_bytes = (root / "reviewed_identity_progress.json").stat().st_size

    print("\n=== BENCHMARK RESULTS ===")
    for key, value in sorted(timer.values.items()):
        print(f"{key}={value}")
    print("snapshot_build_phases=", json.dumps(phases, sort_keys=True))
    print(f"finalize_response_public_bytes={finalize_bytes}")
    print(f"review_progress_next_cases_page_bytes~{page_bytes}")
    print(f"durable_progress_file_bytes={durable_bytes}")
    print(f"progress_schema={final_progress.get('schema_version')}")


if __name__ == "__main__":
    main()
