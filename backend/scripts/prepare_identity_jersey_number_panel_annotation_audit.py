from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_panel_annotation_audit import (  # noqa: E402
    INDEX_FILENAME,
    MANIFEST_FILENAME,
    prepare_panel_annotation_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare a bounded operator audit for jersey number panel boxes."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--selection", type=Path)
    args = parser.parse_args()

    dataset = _read_object(args.dataset.resolve())
    selection = _read_object(args.selection.resolve()) if args.selection else None
    output_root = args.output_root.resolve()
    manifest = prepare_panel_annotation_audit(
        dataset,
        output_root=output_root,
        selection_doc=selection,
    )
    print(json.dumps(manifest["summary"], indent=2, ensure_ascii=False))
    print(f"manifest={output_root / MANIFEST_FILENAME}")
    print(f"audit={output_root / INDEX_FILENAME}")


def _read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


if __name__ == "__main__":
    main()
