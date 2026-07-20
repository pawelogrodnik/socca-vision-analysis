from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_fragment_visual_content import (
    build_identity_fragment_visual_content_evidence,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build advisory endpoint visual-content evidence from reviewed audits.",
    )
    parser.add_argument("--consolidation", type=Path, required=True)
    parser.add_argument("--reviewed-manifest", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    consolidation = _load_json(args.consolidation.resolve())
    reviewed_audits = [
        _load_json(path.resolve()) for path in args.reviewed_manifest
    ]
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = build_identity_fragment_visual_content_evidence(
        consolidation,
        reviewed_audits=reviewed_audits,
    )
    written: dict[str, str] = {}
    for stem, document in outputs.items():
        path = output_dir / f"{stem}.json"
        if path.exists():
            raise FileExistsError(f"Refusing to overwrite existing artifact: {path}")
        path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        written[stem] = str(path)
    print(json.dumps({"written": written}, indent=2))


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
