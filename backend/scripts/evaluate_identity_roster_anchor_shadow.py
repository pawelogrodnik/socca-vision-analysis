from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_roster_anchor_shadow import build_identity_roster_anchor_shadow


PRODUCTION_ARTIFACTS = (
    "global_identity.json",
    "stable_players.json",
    "player_identity_assignments.json",
    "resolved_player_stats.json",
    "player_heatmaps.json",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate P1.15 roster anchors in shadow mode.")
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--assignments", type=Path, required=True)
    parser.add_argument("--match", type=Path, required=True)
    parser.add_argument("--fusion", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    generated_at = datetime.now(timezone.utc).isoformat()
    match_dir = args.match.resolve().parent
    before = production_hashes(match_dir)
    documents = build_identity_roster_anchor_shadow(
        _load(args.candidate),
        _load(args.assignments),
        _load(args.match),
        reid_fusion_doc=_load(args.fusion) if args.fusion else None,
        generated_at=generated_at,
    )
    for name, document in documents.items():
        _write(output_root / f"{name}.json", document)
    after = production_hashes(match_dir)
    evaluation = evaluate_roster_anchor_shadow(documents, before_hashes=before, after_hashes=after)
    evaluation.update(
        {
            "generated_at": generated_at,
            "inputs": {
                "candidate": str(args.candidate.resolve()),
                "assignments": str(args.assignments.resolve()),
                "match": str(args.match.resolve()),
                "fusion": str(args.fusion.resolve()) if args.fusion else None,
            },
        }
    )
    _write(output_root / "p115_roster_anchor_evaluation.json", evaluation)
    (output_root / "P1_15_REPORT.md").write_text(_markdown(evaluation), encoding="utf-8")
    print(json.dumps(evaluation["summary"], indent=2))
    if evaluation["status"] != "passed":
        raise SystemExit(1)


def evaluate_roster_anchor_shadow(
    documents: dict[str, dict[str, Any]],
    *,
    before_hashes: dict[str, str | None] | None = None,
    after_hashes: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    artifact = documents["identity_roster_anchor_shadow"]
    report = documents["identity_roster_anchor_shadow_report"]
    cards = artifact.get("cards") or []
    safety = artifact.get("safety") or {}
    unchanged = (before_hashes or {}) == (after_hashes or {})
    gates = {
        "shadow_mode": artifact.get("mode") == "shadow_read_only",
        "zero_automatic_assignments": safety.get("automatic_assignments") == 0
        and not any(card.get("automatic_assignment") for card in cards),
        "excluded_from_statistics": not safety.get("eligible_for_player_stats")
        and not any(card.get("eligible_for_player_stats") for card in cards),
        "reid_ranking_only": bool(safety.get("reid_is_ranking_only")),
        "production_artifacts_unchanged": unchanged,
        "deterministic_anchor_keys": len({card.get("anchor_key") for card in cards}) == len(cards),
        "parallel_conflicts_not_recommended": all(
            card.get("recommended_player_id") is None
            for card in cards
            if "parallel_roster_candidate_conflict" in (card.get("reason_codes") or [])
        ),
    }
    return {
        "schema_version": "0.1.0",
        "mode": "p115_roster_anchor_shadow_evaluation",
        "status": "passed" if all(gates.values()) else "failed",
        "summary": {
            **(report.get("summary") or {}),
            "production_artifacts_checked": len(before_hashes or {}),
            "production_artifacts_unchanged": unchanged,
        },
        "gates": gates,
        "artifact_hashes": {
            name: {"before": (before_hashes or {}).get(name), "after": (after_hashes or {}).get(name)}
            for name in sorted(set(before_hashes or {}) | set(after_hashes or {}))
        },
    }


def production_hashes(match_dir: Path) -> dict[str, str | None]:
    return {
        name: _sha256(match_dir / name) if (match_dir / name).exists() else None
        for name in PRODUCTION_ARTIFACTS
    }


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.resolve().read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return data


def _write(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, indent=2, sort_keys=True), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _markdown(evaluation: dict[str, Any]) -> str:
    summary = evaluation["summary"]
    gates = evaluation["gates"]
    lines = [
        "# P1.15 Roster Anchor Shadow Evaluation",
        "",
        f"Status: **{evaluation['status']}**",
        "",
        "## Review load",
        "",
        f"- cards: {summary.get('cards', 0)}",
        f"- confirmed manual anchors: {summary.get('confirmed_manual_anchors', 0)}",
        f"- suggested review: {summary.get('suggested_review', 0)}",
        f"- unresolved: {summary.get('unresolved', 0)}",
        f"- conflicts: {summary.get('conflicts', 0)}",
        "",
        "## Safety gates",
        "",
    ]
    lines.extend(f"- [{'x' if passed else ' '}] {name}" for name, passed in gates.items())
    lines.extend(
        [
            "",
            "P1.14 evidence is advisory only. This run does not write roster assignments,",
            "production identity, statistics or heatmaps.",
            "",
        ]
    )
    return "\n".join(lines)


if __name__ == "__main__":
    main()
