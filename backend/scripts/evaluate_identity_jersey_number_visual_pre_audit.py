from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_visual_pre_audit import (
    build_identity_jersey_number_visual_pre_audit,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build shadow-only jersey visual pre-audit suggestions.")
    parser.add_argument("--subject-review", type=Path, required=True)
    parser.add_argument("--crop-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    subject_review = json.loads(args.subject_review.read_text(encoding="utf-8"))
    if not isinstance(subject_review, dict):
        raise ValueError("subject-review must contain a JSON object")
    artifact = build_identity_jersey_number_visual_pre_audit(
        subject_review,
        crop_root=args.crop_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
