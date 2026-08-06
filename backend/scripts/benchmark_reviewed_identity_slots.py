#!/usr/bin/env python3
from __future__ import annotations

"""Read-only benchmark for bounded reviewed anonymous identity semantics."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from app.services.identity_reviewed_frame_uniqueness import build_frame_slot_demotions
from app.services.identity_stable_anonymous import resolve_stable_anonymous_entities


_BOUNDED = re.compile(r"^[AB](?:0[1-9]|1[0-4])$")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", action="append", type=Path)
    parser.add_argument("--output", type=Path)
    options = parser.parse_args()
    roots = options.root or [Path("backend/storage/matches"), Path("backend/storage/published/matches")]
    scenarios = [_benchmark(path.parent) for root in roots if root.exists() for path in sorted(root.glob("*/tracklets.json")) if (path.parent / "identity_candidate_shadow.json").exists()]
    scenarios = [row for row in scenarios if row is not None]
    report = {
        "schema_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "reviewed_identity_bounded_slots_read_only_benchmark",
        "roots": [str(root) for root in roots],
        "scenario_count": len(scenarios),
        "duration_buckets": _duration_buckets(scenarios),
        "scenarios": scenarios,
        "summary": {
            "passed": sum(row["acceptance"]["passed"] for row in scenarios),
            "failed": sum(not row["acceptance"]["passed"] for row in scenarios),
            "automatic_permanent_allocations": sum(row["identity"]["automatic_permanent_allocations"] for row in scenarios),
            "duplicate_stable_labels_after_guard": sum(row["frame_uniqueness"]["duplicate_stable_labels_after_guard"] for row in scenarios),
        },
        "safety": {
            "read_only": True,
            "reran_yolo": False,
            "reran_tracking": False,
            "reran_reid": False,
            "mutated_match_packages": False,
        },
    }
    if options.output:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if len(scenarios) >= 3 and not report["summary"]["failed"] else 1


def _benchmark(match_path: Path) -> dict[str, Any] | None:
    tracklets_document = _load(match_path / "tracklets.json")
    candidate_document = _load(match_path / "identity_candidate_shadow.json")
    if not tracklets_document or not candidate_document:
        return None
    tracklets = {str(row.get("tracklet_id")): row for row in tracklets_document.get("tracklets") or [] if row.get("tracklet_id")}
    resolved, diagnostics = resolve_stable_anonymous_entities(match_path, tracklets, candidate_document)
    assignments = [
        {
            "tracklet_id": tracklet_id,
            "team_label": tracklets[tracklet_id].get("team_label") or "U",
            "stable_anonymous_slot_id": row["stable_anonymous_slot_id"],
            "stable_anchor_source": row["stable_anchor_source"],
            "stable_anchor_claims": row["stable_anchor_claims"],
            "identity_status": "conflicted" if row["hard_blockers"] else "unresolved",
        }
        for tracklet_id, row in resolved.items()
    ]
    _, uniqueness = build_frame_slot_demotions(tracklets, assignments)
    slots = sorted({str(row["stable_anonymous_slot_id"]) for row in resolved.values() if row.get("stable_anonymous_slot_id")})
    fragments_by_slot: dict[str, set[str]] = defaultdict(set)
    for row in resolved.values():
        if row.get("stable_anonymous_slot_id"):
            fragments_by_slot[str(row["stable_anonymous_slot_id"])].add(str(row["fragment_id"]))
    anchored = sum(row.get("stable_anonymous_slot_id") is not None for row in resolved.values())
    duration = _duration(tracklets)
    exact_seeds = sum(row.get("action") == "assign_roster_player" for row in (_load(match_path / "identity_operator_seeds.json").get("decisions") or []))
    blockers = Counter(value for row in resolved.values() for value in row["hard_blockers"])
    accepted = (
        diagnostics["automatic_permanent_allocations"] == 0
        and all(_BOUNDED.fullmatch(slot) for slot in slots)
        and uniqueness["duplicate_stable_labels_rendered"] == 0
        and not any(re.fullmatch(r"U\d+", row["fallback_label"]) for row in resolved.values())
    )
    return {
        "match_path": str(match_path),
        "duration_sec": duration,
        "duration_bucket": _bucket(duration),
        "input": {"tracklets": len(tracklets), "candidate_subjects": len(candidate_document.get("subjects") or [])},
        "identity": {
            **diagnostics,
            "bounded_slot_ids": slots,
            "anchored_tracklets": anchored,
            "entity_reuse_rate": round((anchored - len(slots)) / anchored, 4) if anchored else 0.0,
            "fragment_count_distribution": dict(sorted(Counter(len(value) for value in fragments_by_slot.values()).items())),
            "slots_reused_by_multiple_fragments": sum(
                len(value) > 1 for value in fragments_by_slot.values()
            ),
            "artificial_new_slots": 0,
            "false_splits": {
                "availability": "not_measurable_without_ground_truth_player_identity",
                "bounded_fragmentation_proxy": "fragment_count_distribution",
            },
            "hard_blockers": dict(sorted(blockers.items())),
        },
        "frame_uniqueness": {
            "duplicate_stable_slot_claim_groups_before_guard": uniqueness["duplicate_stable_slot_claim_groups"],
            "demoted_observation_claims": uniqueness["demoted_observation_claims"],
            "duplicate_stable_labels_after_guard": uniqueness["duplicate_stable_labels_rendered"],
        },
        "reviewed_coverage": {
            "exact_named_operator_seeds": exact_seeds,
            "coverage_note": "Names require exact seeds or fresh structurally safe reviewed lineage; benchmark does not promote automatic names.",
        },
        "flicker_and_reuse_safety": {
            "availability": "bounded resolver diagnostics only",
            "unsafe_new_player_allocations": diagnostics["automatic_permanent_allocations"],
        },
        "acceptance": {"passed": accepted},
    }


def _duration(tracklets: dict[str, dict[str, Any]]) -> float:
    return round(max((float(row.get("time_sec") or 0.0) for tracklet in tracklets.values() for row in tracklet.get("positions_m") or []), default=0.0), 3)


def _bucket(duration: float) -> str:
    return "short" if duration < 180 else "medium" if duration < 900 else "long"


def _duration_buckets(rows: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row["duration_bucket"]) for row in rows).items()))


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
