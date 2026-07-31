from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_bounded_h2_reid_followup import (
    verify_frozen_bounded_h2_rankings,
)
from app.services.identity_jersey_number_common import canonical_digest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify immutable bounded-H2 frozen rankings independently.",
    )
    parser.add_argument("--session-path", type=Path, required=True)
    parser.add_argument("--output-path", type=Path, required=True)
    args = parser.parse_args()

    session_path = args.session_path.resolve()
    selection = _load(session_path / "bounded_h2_selection.json")
    frozen = _load(session_path / "preferred_rankings_frozen.json")
    decisions = _load(session_path / "operator_decisions.json")
    artifact = {
        **verify_frozen_bounded_h2_rankings(selection, frozen),
        "operator_decisions_digest": canonical_digest(decisions),
        "operator_decision_count": len(decisions.get("decisions") or []),
        "operator_session_finished": bool(decisions.get("finished")),
        "source_session_path": str(session_path),
        "immutable_source_mutated": False,
    }
    _write(args.output_path.resolve(), artifact)
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
