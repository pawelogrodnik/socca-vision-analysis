from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_jersey_number_discovery_audit import (  # noqa: E402
    apply_jersey_number_discovery_audit,
    build_discovery_dataset_from_subject_review,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply a reviewed jersey-number discovery gate to a canonical dataset."
    )
    parser.add_argument(
        "--match-dir",
        type=Path,
        required=True,
        help="Match directory containing match.json and identity roster subject review.",
    )
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--team-label", default="A")
    args = parser.parse_args()

    match_dir = args.match_dir.resolve()
    match = _read_object(match_dir / "match.json")
    review = _read_object(match_dir / "identity_roster_subject_review_shadow.json")
    dataset = build_discovery_dataset_from_subject_review(
        review,
        artifact_root=match_dir,
        source_match_key=str(match.get("id") or match_dir.name),
        source_video_key=str(match.get("video_filename") or match.get("id") or match_dir.name),
        team_label_value=args.team_label,
    )
    reviewed = _read_object(args.reviewed.resolve())
    applied = apply_jersey_number_discovery_audit(dataset, reviewed)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(applied, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(applied.get("summary") or {}, indent=2, ensure_ascii=False))
    print(f"dataset={output}")


def _read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


if __name__ == "__main__":
    main()
