from __future__ import annotations

"""Official local smoke command for the reviewed-output product contract."""

import argparse
import json
import time
from pathlib import Path

from app.services.identity_reviewed_output_jobs import generate_reviewed_output, reviewed_output_status
from app.services.identity_reviewed_snapshot import finalize_reviewed_identity
from app.services.identity_initial_audit_store import production_identity_snapshot
from app.services.identity_reviewed_output_qa import build_reviewed_output_qa


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--match-root", required=True, type=Path); parser.add_argument("--minimap", action="store_true"); options = parser.parse_args()
    match = json.loads((options.match_root / "match.json").read_text(encoding="utf-8")); production_before = production_identity_snapshot(options.match_root, match); snapshot = finalize_reviewed_identity(options.match_root, match)
    job = generate_reviewed_output(options.match_root, snapshot, match, {"include_minimap": options.minimap, "include_ball": True, "show_roster_number": False})
    while job.get("status") in {"queued", "running"}:
        time.sleep(.25); job = reviewed_output_status(options.match_root, snapshot)
    if job.get("status") == "completed":
        qa = build_reviewed_output_qa(
            options.match_root,
            snapshot,
            job,
            production_before=production_before,
            production_after=production_identity_snapshot(options.match_root, match),
        )
    print(json.dumps({"snapshot_status": snapshot["status"], "snapshot_digest": snapshot["semantic_digest"], "job": job}, indent=2))
    return 0 if job.get("status") == "completed" and qa.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
