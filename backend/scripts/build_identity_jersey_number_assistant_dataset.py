from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_assistant_audit_dataset import (
    build_assistant_audit_dataset_source,
)
from app.services.identity_jersey_number_dataset import (
    build_identity_jersey_number_dataset_manifest,
)
from app.services.identity_roster_subject_review_store import REVIEW_ARTIFACT_FILENAME


ASSISTANT_VISUAL_AUDIT_FILENAMES = (
    "identity_jersey_number_assistant_visual_audit_shadow.json",
    "assistant_visual_audit_shadow.json",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build diagnostic single-match assistant jersey dataset.")
    parser.add_argument("--match-dir", type=Path, required=True)
    parser.add_argument(
        "--roster-reference",
        type=Path,
        default=REPOSITORY_ROOT / "examples/corgi_jersey_numbers.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    match_dir = args.match_dir.resolve()
    match_doc = _load(match_dir / "match.json")
    subject_review = _load(match_dir / REVIEW_ARTIFACT_FILENAME)
    assistant_audit = _load_assistant_audit(match_dir)
    roster_reference = _load(args.roster_reference)
    source_match_key = _match_id(match_doc)
    source_video_key = _video_key(match_doc)
    adapter = build_assistant_audit_dataset_source(
        subject_review,
        assistant_audit,
        source_match_key=source_match_key,
        roster_reference=roster_reference,
    )
    manifest = build_identity_jersey_number_dataset_manifest(
        [
            {
                "source_match_key": source_match_key,
                "source_video_key": source_video_key,
                "crop_root": match_dir,
                "cards_doc": adapter["cards_doc"],
                "reviewed_observations_doc": adapter["reviewed_observations_doc"],
            }
        ]
    )
    document = {
        "mode": "diagnostic_single_match_dataset",
        "safety": {
            "diagnostic_only": True,
            "writes_player_identity_assignments": False,
            "mutates_candidate_identity": False,
            "mutates_production_identity": False,
        },
        "source_adapter_provenance": adapter["provenance"],
        "roster_reference": {
            "path": str(args.roster_reference),
            "digest": adapter["provenance"]["roster_reference_digest"],
            "policy": adapter["provenance"]["roster_reference_policy"],
        },
        "dataset_manifest": manifest,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _load_assistant_audit(match_dir: Path) -> dict[str, Any]:
    for filename in ASSISTANT_VISUAL_AUDIT_FILENAMES:
        path = match_dir / filename
        if path.is_file():
            return _load(path)
    raise FileNotFoundError("assistant visual audit artifact not found")


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object: {path.name}")
    return value


def _match_id(match_doc: dict[str, Any]) -> str:
    for field in ("match_id", "id"):
        value = str(match_doc.get(field) or "").strip()
        if value:
            return value
    raise ValueError("match.json requires match_id or id")


def _video_key(match_doc: dict[str, Any]) -> str:
    metadata = match_doc.get("metadata")
    sources = (match_doc, metadata) if isinstance(metadata, dict) else (match_doc,)
    for field in ("source_video_key", "video_filename", "video_path", "video"):
        for source in sources:
            value = source.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    raise ValueError("match.json requires an explicit video filename or path")


if __name__ == "__main__":
    main()
