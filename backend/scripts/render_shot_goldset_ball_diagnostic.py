from __future__ import annotations

"""Render a local-only logical ball-evidence diagnostic from reviewed videos."""

import argparse
import json
from pathlib import Path

from app.config import STORAGE_DIR
from app.services.reviewed_ball_diagnostic import (
    concatenate_reviewed_ball_diagnostics,
    render_reviewed_ball_diagnostic,
)


DEFAULT_GROUP_ID = "match-group-c3fbd48a-356d-44a0-a740-c630de69b527"
DEFAULT_OUTPUT = (
    STORAGE_DIR
    / "benchmarks"
    / "shot-candidates-shadow-v1"
    / "corgi-verisk"
    / "corgi_verisk_ball_bbox_diagnostic.mp4"
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group-id", default=DEFAULT_GROUP_ID)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    diagnostic_root = (STORAGE_DIR / "benchmarks").resolve()
    if diagnostic_root not in args.output.resolve().parents:
        raise ValueError("diagnostic_output_must_be_under_storage_benchmarks")

    group_path = STORAGE_DIR / "published" / "match-groups" / args.group_id / "manifest.json"
    group = json.loads(group_path.read_text(encoding="utf-8"))
    members = sorted(group.get("members") or [], key=lambda member: int(member.get("sequence_index") or 0))
    if not members:
        raise RuntimeError("diagnostic_group_has_no_members")
    physical_outputs: list[Path] = []
    source_records: list[dict] = []
    try:
        for member in members:
            match_id = str(member["source_match_id"])
            match_path = STORAGE_DIR / "matches" / match_id
            physical_output = args.output.with_name(f".{args.output.stem}.{int(member['sequence_index']):02d}.{match_id}.mp4")
            physical_outputs.append(physical_output)
            metadata = render_reviewed_ball_diagnostic(
                match_path / "reviewed_video.mp4",
                json.loads((match_path / "ball_tracks.json").read_text(encoding="utf-8")),
                physical_output,
                target_duration_sec=float(member["logical_end_sec"]) - float(member["logical_start_sec"]),
            )
            source_records.append(
                {
                    "source_match_id": match_id,
                    "sequence_index": int(member["sequence_index"]),
                    "logical_start_sec": float(member["logical_start_sec"]),
                    "logical_end_sec": float(member["logical_end_sec"]),
                    "diagnostic": metadata,
                }
            )
        result = concatenate_reviewed_ball_diagnostics(physical_outputs, args.output)
    finally:
        for physical_output in physical_outputs:
            physical_output.unlink(missing_ok=True)

    manifest_path = args.output.with_suffix(".json")
    manifest_path.write_text(
        json.dumps(
            {
                "kind": "local_shot_goldset_ball_diagnostic",
                "group_id": args.group_id,
                "logical_timeline_span_sec": float((group.get("timing") or {}).get("timeline_span_sec") or 0.0),
                "sources": source_records,
                "output": {"path": str(args.output), **result},
                "safety": {
                    "reran_yolo": False,
                    "reran_tracking": False,
                    "canonical_or_public_video_modified": False,
                    "video_qa_modified": False,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "manifest": str(manifest_path), **result}))


if __name__ == "__main__":
    main()
