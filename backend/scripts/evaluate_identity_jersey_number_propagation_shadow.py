from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_propagation_shadow import (
    build_identity_jersey_number_propagation_shadow,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate N5 jersey-number propagation from existing frozen N0-N4 artifacts."
    )
    parser.add_argument("--assignment", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--candidate-identity", type=Path, required=True)
    parser.add_argument("--shadow-timeline", type=Path, required=True)
    parser.add_argument("--subject-review", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    document = build_identity_jersey_number_propagation_shadow(
        _load(args.assignment),
        _load(args.evidence),
        _load(args.candidate_identity),
        _load(args.shadow_timeline),
        subject_review_doc=_load(args.subject_review) if args.subject_review else None,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    _write(output_root / "identity_jersey_number_propagation_shadow.json", document)
    (output_root / "N5_JERSEY_NUMBER_PROPAGATION_REPORT.md").write_text(
        _markdown(document),
        encoding="utf-8",
    )
    print(json.dumps(document.get("summary") or {}, indent=2, sort_keys=True))


def _load(path: Path) -> dict[str, Any]:
    document = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return document


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _markdown(document: dict[str, Any]) -> str:
    summary = document.get("summary") or {}
    safety = document.get("safety") or {}
    return "\n".join(
        [
            "# N5 Jersey Number Propagation Shadow Report",
            "",
            f"- Seed subjects: {summary.get('seed_subjects', 0)}",
            f"- Seed tracklets: {summary.get('seed_tracklets', 0)}",
            f"- Propagated tracklets: {summary.get('propagated_tracklets', 0)}",
            f"- Subjects with propagation: {summary.get('subjects_with_propagation', 0)}",
            f"- Safe explicit edges: {summary.get('safe_edges', 0)}",
            f"- Blocked edges: {summary.get('blocked_edges', 0)}",
            f"- Cross-subject propagations: {summary.get('cross_subject_propagations', 0)}",
            f"- Automatic assignments: {safety.get('automatic_assignments', 0)}",
            "",
            "This benchmark is shadow-only and does not mutate candidate or production identity.",
        ]
    )


if __name__ == "__main__":
    main()
