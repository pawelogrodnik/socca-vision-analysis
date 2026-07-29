#!/usr/bin/env python3
"""Merge audited jersey-number discovery datasets without duplicating crops."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.identity_jersey_number_discovery_audit import combine_discovery_datasets  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        action="append",
        required=True,
        help="Audited canonical dataset JSON. Repeat for every source.",
    )
    parser.add_argument("--output", required=True, help="Path for the merged canonical dataset JSON.")
    args = parser.parse_args()

    source_paths = [Path(value).expanduser().resolve() for value in args.dataset]
    datasets = [_load_json(path) for path in source_paths]
    combined = combine_discovery_datasets(*datasets)
    combined["consolidation"] = {
        "source_paths": [str(path) for path in source_paths],
        "deduplication_key": "source_match_key + source_video_key + artifact",
        "manual_labels_preferred": True,
        "collection_readiness": _collection_readiness(combined.get("samples") or []),
    }

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    summary = combined.get("summary") or {}
    print(json.dumps({
        "output": str(output_path),
        "samples": summary.get("samples", 0),
        "states": summary.get("states", {}),
        "numbers": summary.get("numbers", {}),
        "readiness": combined["consolidation"]["collection_readiness"],
    }, ensure_ascii=False, indent=2))
    return 0


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("samples"), list):
        raise ValueError(f"Unsupported discovery dataset: {path}")
    return payload


def _collection_readiness(samples: list[object]) -> dict[str, object]:
    rows = [row for row in samples if isinstance(row, dict)]
    confirmed = [
        row for row in rows
        if str(row.get("jersey_number_state") or row.get("label_state") or "") == "number_confirmed"
        and (row.get("jersey_number") is not None or row.get("number") is not None)
    ]
    negatives = [
        row for row in rows
        if str(row.get("jersey_number_state") or row.get("label_state") or "")
        in {"number_absent", "number_unreadable"}
    ]
    episodes = {
        (
            str(row.get("source_match_key") or ""),
            str(row.get("source_video_key") or ""),
            str(row.get("visibility_episode_id") or row.get("sample_key") or ""),
        )
        for row in confirmed
    }
    target_confirmed = 50
    target_negatives = 30
    return {
        "confirmed_panels": len(confirmed),
        "negative_panels": len(negatives),
        "confirmed_visibility_episodes": len(episodes),
        "targets": {"confirmed_panels": target_confirmed, "negative_panels": target_negatives},
        "ready_for_panel_model_evaluation": len(confirmed) >= target_confirmed and len(negatives) >= target_negatives,
        "remaining_confirmed_needed": max(0, target_confirmed - len(confirmed)),
        "remaining_negative_needed": max(0, target_negatives - len(negatives)),
    }


if __name__ == "__main__":
    raise SystemExit(main())
