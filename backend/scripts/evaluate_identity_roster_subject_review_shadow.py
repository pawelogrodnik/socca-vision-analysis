from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_roster_subject_review_shadow import (
    build_identity_roster_subject_review_shadow,
)


PRODUCTION_ARTIFACTS = (
    "global_identity.json",
    "stable_players.json",
    "player_identity_assignments.json",
    "resolved_player_stats.json",
    "player_heatmaps.json",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate P1.17 whole-subject roster review contract.")
    parser.add_argument("--roster-anchor", type=Path, required=True)
    parser.add_argument("--anchor-crops", type=Path, required=True)
    parser.add_argument("--match-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    generated_at = datetime.now(timezone.utc).isoformat()
    before = production_hashes(args.match_dir.resolve())
    roster_anchor = _load(args.roster_anchor)
    anchor_crops = _load(args.anchor_crops)
    documents = build_identity_roster_subject_review_shadow(
        roster_anchor,
        anchor_crops,
        generated_at=generated_at,
    )
    repeated = build_identity_roster_subject_review_shadow(
        roster_anchor,
        anchor_crops,
        generated_at=generated_at,
    )
    for name, document in documents.items():
        _write(output_root / f"{name}.json", document)
    after = production_hashes(args.match_dir.resolve())
    evaluation = evaluate_identity_roster_subject_review_shadow(
        documents,
        before_hashes=before,
        after_hashes=after,
        deterministic=documents == repeated,
    )
    evaluation.update(
        {
            "generated_at": generated_at,
            "inputs": {
                "roster_anchor": str(args.roster_anchor.resolve()),
                "anchor_crops": str(args.anchor_crops.resolve()),
                "match_dir": str(args.match_dir.resolve()),
            },
        }
    )
    _write(output_root / "p117_subject_review_evaluation.json", evaluation)
    (output_root / "P1_17_REPORT.md").write_text(_markdown(evaluation), encoding="utf-8")
    (output_root / "index.html").write_text(
        _gallery_html(documents["identity_roster_subject_review_shadow"], evaluation),
        encoding="utf-8",
    )
    print(json.dumps(evaluation["summary"], indent=2))
    if evaluation["status"] != "passed":
        raise SystemExit(1)


def evaluate_identity_roster_subject_review_shadow(
    documents: dict[str, dict[str, Any]],
    *,
    before_hashes: dict[str, str | None] | None = None,
    after_hashes: dict[str, str | None] | None = None,
    deterministic: bool = True,
) -> dict[str, Any]:
    artifact = documents["identity_roster_subject_review_shadow"]
    report = documents["identity_roster_subject_review_shadow_report"]
    cards = artifact.get("cards") or []
    unchanged = (before_hashes or {}) == (after_hashes or {})
    unique_card_keys = len({card.get("review_card_key") for card in cards}) == len(cards)
    gates = {
        "shadow_mode": artifact.get("mode") == "shadow_read_only",
        "whole_subject_review_unit": all(card.get("review_unit") == "candidate_stable_subject" for card in cards),
        "no_single_crop_assignment_action": all(
            "assign_single_crop" not in (card.get("allowed_actions") or []) for card in cards
        ),
        "zero_automatic_assignments": (artifact.get("safety") or {}).get("automatic_assignments") == 0,
        "excluded_from_statistics": not (artifact.get("safety") or {}).get("eligible_for_player_stats"),
        "production_artifacts_unchanged": unchanged,
        "deterministic_output": deterministic,
        "unique_review_card_keys": unique_card_keys,
        "conflicts_block_confirmation": all(
            "confirm_recommended_player" not in (card.get("allowed_actions") or [])
            for card in cards
            if card.get("review_status") == "blocked_conflict"
        ),
    }
    return {
        "schema_version": "0.1.0",
        "mode": "p117_subject_review_shadow_evaluation",
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
    lines = [
        "# P1.17 Whole-Subject Review Contract Evaluation",
        "",
        f"Status: **{evaluation['status']}**",
        "",
        "## Contract",
        "",
        f"- cards: {summary.get('cards', 0)}",
        f"- ready for operator review: {summary.get('ready_for_operator_review', 0)}",
        f"- blocked conflicts: {summary.get('blocked_conflicts', 0)}",
        f"- needs more visual evidence: {summary.get('needs_more_visual_evidence', 0)}",
        f"- no visual evidence: {summary.get('no_visual_evidence', 0)}",
        f"- cards with recommended player: {summary.get('cards_with_recommended_player', 0)}",
        f"- selected crops referenced: {summary.get('selected_crops', 0)}",
        "",
        "## Safety gates",
        "",
    ]
    lines.extend(f"- [{'x' if passed else ' '}] {name}" for name, passed in evaluation["gates"].items())
    lines.extend(
        [
            "",
            "This contract is still shadow/read-only. It describes operator decisions",
            "for whole candidate stable subjects and never writes roster assignments.",
            "",
        ]
    )
    return "\n".join(lines)


def _gallery_html(artifact: dict[str, Any], evaluation: dict[str, Any]) -> str:
    rows: list[str] = []
    for card in artifact.get("cards") or []:
        evidence = card.get("visual_evidence") or {}
        crops = evidence.get("anchor_crops") or []
        crops_html = "".join(
            "<figure><img loading='lazy' src='{}' alt='frame {}'><figcaption>f{} score {:.3f}</figcaption></figure>".format(
                html.escape(str(crop.get("artifact") or "")),
                int(crop.get("frame") or 0),
                int(crop.get("frame") or 0),
                float(crop.get("selection_score") or 0.0),
            )
            for crop in crops
        ) or "<p class='empty'>No visual evidence</p>"
        recommended = card.get("recommended_player") or {}
        blockers = ", ".join(str(value) for value in card.get("blockers") or []) or "none"
        actions = ", ".join(str(value) for value in card.get("allowed_actions") or [])
        rows.append(
            "<article class='{}'><header><strong>{}</strong><span>{} | {} | {}</span></header>"
            "<p>recommended: <b>{}</b> | blockers: {} | actions: {}</p><div class='crops'>{}</div></article>".format(
                html.escape(str(card.get("review_status") or "")),
                html.escape(str(card.get("candidate_subject_id") or "")),
                html.escape(str(card.get("team_label") or "U")),
                html.escape(str(card.get("review_status") or "")),
                html.escape(str(card.get("roster_status") or "")),
                html.escape(str(recommended.get("player_name") or recommended.get("player_id") or "none")),
                html.escape(blockers),
                html.escape(actions),
                crops_html,
            )
        )
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>P1.17 whole-subject review contract</title><style>
body{margin:0;background:#0b1220;color:#e7edf7;font:15px system-ui,sans-serif}main{max-width:1500px;margin:auto;padding:24px}
h1{font-size:24px}article{border-top:1px solid #334155;padding:18px 0}header{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
header span,p,figcaption,.empty{color:#9fb0c8}.blocked_conflict header strong{color:#fb7185}.ready_for_operator_review header strong{color:#86efac}
.crops{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:12px}
figure{margin:0;background:#111c2e;padding:8px;border-radius:6px}img{width:100%;height:220px;object-fit:contain;background:#020617}figcaption{padding-top:6px}
</style></head><body><main><h1>P1.17 whole-subject review contract</h1><p>Status: <strong>""" + html.escape(str(evaluation["status"])) + """</strong></p>""" + "".join(rows) + """</main></body></html>"""


if __name__ == "__main__":
    main()
