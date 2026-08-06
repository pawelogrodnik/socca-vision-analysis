from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.identity_initial_audit_store import write_identity_json_atomic
from app.services.identity_stable_anonymous import resolve_stable_anonymous_entities


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose candidate-fragment to stable-anonymous identity mapping.")
    parser.add_argument("--match-root", required=True, type=Path)
    parser.add_argument("--write-report", action="store_true")
    options = parser.parse_args()
    tracklets_document = _load(options.match_root / "tracklets.json")
    candidate_document = _load(options.match_root / "identity_candidate_shadow.json")
    tracklets = {
        str(row.get("tracklet_id")): row
        for row in tracklets_document.get("tracklets") or []
        if row.get("tracklet_id")
    }
    resolved, diagnostics = resolve_stable_anonymous_entities(
        options.match_root, tracklets, candidate_document
    )
    report = {
        "schema_version": "1.0.0",
        "match_id": options.match_root.name,
        **diagnostics,
        "tracklets_total": len(tracklets),
        "tracklets_with_stable_anchor": sum(
            row.get("stable_anchor_source") not in {"deterministic_new_allocation", "ephemeral_short_fragment"}
            for row in resolved.values()
        ),
        "hard_conflict_tracklets": sum(bool(row.get("hard_blockers")) for row in resolved.values()),
        "safety": {"production_identity_mutated": False},
    }
    if options.write_report:
        write_identity_json_atomic(
            options.match_root / "reviewed_identity_fragmentation_diagnostic.json",
            report,
        )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
