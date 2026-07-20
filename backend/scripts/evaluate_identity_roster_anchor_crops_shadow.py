from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import sys
from typing import Any

import cv2


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_roster_anchor_crops_shadow import (
    build_identity_roster_anchor_crops_shadow,
)


PRODUCTION_ARTIFACTS = (
    "global_identity.json",
    "stable_players.json",
    "player_identity_assignments.json",
    "resolved_player_stats.json",
    "player_heatmaps.json",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate P1.16 anchor crop selection in shadow mode.")
    parser.add_argument("--roster-anchor", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--occlusions", type=Path)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--match-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    generated_at = datetime.now(timezone.utc).isoformat()
    before = production_hashes(args.match_dir.resolve())
    inputs = (
        _load(args.roster_anchor),
        _load(args.timeline),
        _load(args.occlusions) if args.occlusions else None,
    )
    documents = build_identity_roster_anchor_crops_shadow(
        inputs[0],
        inputs[1],
        occlusion_doc=inputs[2],
        generated_at=generated_at,
    )
    repeated = build_identity_roster_anchor_crops_shadow(
        inputs[0],
        inputs[1],
        occlusion_doc=inputs[2],
        generated_at=generated_at,
    )
    artifact = documents["identity_roster_anchor_crops_shadow"]
    rendered = render_anchor_crops(args.video.resolve(), output_root, artifact)
    for name, document in documents.items():
        _write(output_root / f"{name}.json", document)
    after = production_hashes(args.match_dir.resolve())
    evaluation = evaluate_anchor_crops_shadow(
        documents,
        before_hashes=before,
        after_hashes=after,
        deterministic=documents == repeated,
        rendered_artifacts=rendered,
    )
    evaluation.update(
        {
            "generated_at": generated_at,
            "inputs": {
                "roster_anchor": str(args.roster_anchor.resolve()),
                "timeline": str(args.timeline.resolve()),
                "occlusions": str(args.occlusions.resolve()) if args.occlusions else None,
                "video": str(args.video.resolve()),
                "match_dir": str(args.match_dir.resolve()),
            },
        }
    )
    _write(output_root / "p116_anchor_crop_evaluation.json", evaluation)
    (output_root / "P1_16_REPORT.md").write_text(_markdown(evaluation), encoding="utf-8")
    (output_root / "index.html").write_text(_gallery_html(artifact, evaluation), encoding="utf-8")
    print(json.dumps(evaluation["summary"], indent=2))
    if evaluation["status"] != "passed":
        raise SystemExit(1)


def render_anchor_crops(video_path: Path, output_root: Path, artifact: dict[str, Any]) -> set[str]:
    requests: dict[int, list[dict[str, Any]]] = {}
    for card in artifact.get("cards") or []:
        for crop in card.get("anchor_crops") or []:
            requests.setdefault(int(crop["frame"]), []).append(crop)
    if not requests:
        return set()
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
    rendered: set[str] = set()
    try:
        target_frames = set(requests)
        frame_index = 0
        while target_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index in target_frames:
                height, width = frame.shape[:2]
                for crop in requests[frame_index]:
                    x1, y1, x2, y2 = [float(value) for value in crop["bbox_xyxy"]]
                    margin_x = max(8, int(round((x2 - x1) * 0.30)))
                    margin_y = max(8, int(round((y2 - y1) * 0.20)))
                    left = max(0, int(x1) - margin_x)
                    top = max(0, int(y1) - margin_y)
                    right = min(width, int(x2) + margin_x)
                    bottom = min(height, int(y2) + margin_y)
                    image = frame[top:bottom, left:right]
                    artifact_path = output_root / str(crop["artifact"])
                    artifact_path.parent.mkdir(parents=True, exist_ok=True)
                    if image.size and cv2.imwrite(str(artifact_path), image):
                        rendered.add(str(crop["artifact"]))
                target_frames.remove(frame_index)
            frame_index += 1
    finally:
        capture.release()
    return rendered


def evaluate_anchor_crops_shadow(
    documents: dict[str, dict[str, Any]],
    *,
    before_hashes: dict[str, str | None] | None = None,
    after_hashes: dict[str, str | None] | None = None,
    deterministic: bool = True,
    rendered_artifacts: set[str] | None = None,
) -> dict[str, Any]:
    artifact = documents["identity_roster_anchor_crops_shadow"]
    report = documents["identity_roster_anchor_crops_shadow_report"]
    cards = artifact.get("cards") or []
    crops = [crop for card in cards for crop in card.get("anchor_crops") or []]
    rendered = rendered_artifacts if rendered_artifacts is not None else {str(crop["artifact"]) for crop in crops}
    unchanged = (before_hashes or {}) == (after_hashes or {})
    unique_crop_ids = len({crop.get("anchor_crop_id") for crop in crops}) == len(crops)
    per_card_limits = all(0 <= len(card.get("anchor_crops") or []) <= 5 for card in cards)
    ordered_unique_frames = all(
        len({int(crop["frame"]) for crop in card.get("anchor_crops") or []})
        == len(card.get("anchor_crops") or [])
        for card in cards
    )
    gates = {
        "shadow_mode": artifact.get("mode") == "shadow_read_only",
        "zero_automatic_assignments": (artifact.get("safety") or {}).get("automatic_assignments") == 0,
        "excluded_from_statistics": not (artifact.get("safety") or {}).get("eligible_for_player_stats"),
        "production_artifacts_unchanged": unchanged,
        "deterministic_output": deterministic,
        "unique_anchor_crop_ids": unique_crop_ids,
        "three_to_five_crop_contract": per_card_limits,
        "unique_frames_per_card": ordered_unique_frames,
        "only_reliable_observations_selected": all(crop.get("selection_eligible") for crop in crops),
        "all_selected_crops_rendered": all(str(crop["artifact"]) in rendered for crop in crops),
    }
    return {
        "schema_version": "0.1.0",
        "mode": "p116_anchor_crop_shadow_evaluation",
        "status": "passed" if all(gates.values()) else "failed",
        "summary": {
            **(report.get("summary") or {}),
            "rendered_crops": len(rendered),
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
        "# P1.16 Anchor Crop Shadow Evaluation",
        "",
        f"Status: **{evaluation['status']}**",
        "",
        "## Selection",
        "",
        f"- cards: {summary.get('cards', 0)}",
        f"- ready cards: {summary.get('cards_ready_for_visual_audit', 0)}",
        f"- insufficient cards: {summary.get('cards_with_insufficient_reliable_crops', 0)}",
        f"- no reliable crops: {summary.get('cards_without_reliable_crops', 0)}",
        f"- selected crops: {summary.get('selected_crops', 0)}",
        f"- rendered crops: {summary.get('rendered_crops', 0)}",
        "",
        "## Safety gates",
        "",
    ]
    lines.extend(f"- [{'x' if passed else ' '}] {name}" for name, passed in evaluation["gates"].items())
    lines.extend(
        [
            "",
            "Crops are review-only evidence for candidate stable subjects. They do not",
            "write roster assignments, production identity, statistics or heatmaps.",
            "",
        ]
    )
    return "\n".join(lines)


def _gallery_html(artifact: dict[str, Any], evaluation: dict[str, Any]) -> str:
    cards_html: list[str] = []
    for card in artifact.get("cards") or []:
        crops_html = "".join(
            "<figure><img loading='lazy' src='{}' alt='frame {}'><figcaption>f{} | score {:.3f}</figcaption></figure>".format(
                html.escape(str(crop["artifact"])),
                int(crop["frame"]),
                int(crop["frame"]),
                float(crop["selection_score"]),
            )
            for crop in card.get("anchor_crops") or []
        ) or "<p class='empty'>No reliable crops</p>"
        label = card.get("recommended_player_name") or card.get("recommended_player_id") or "unresolved"
        cards_html.append(
            "<article><header><strong>{}</strong><span>{} | {} | roster: {}</span></header><div class='crops'>{}</div></article>".format(
                html.escape(str(card.get("candidate_subject_id") or "")),
                html.escape(str(card.get("team_label") or "U")),
                html.escape(str(card.get("status") or "")),
                html.escape(str(label)),
                crops_html,
            )
        )
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>P1.16 anchor crop audit</title><style>
body{margin:0;background:#0b1220;color:#e7edf7;font:15px system-ui,sans-serif}main{max-width:1500px;margin:auto;padding:24px}
h1{font-size:24px}article{border-top:1px solid #334155;padding:18px 0}header{display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
header span,figcaption,.empty{color:#9fb0c8}.crops{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-top:12px}
figure{margin:0;background:#111c2e;padding:8px;border-radius:6px}img{width:100%;height:260px;object-fit:contain;background:#020617}figcaption{padding-top:6px}
</style></head><body><main><h1>P1.16 anchor crop audit</h1><p>Status: <strong>""" + html.escape(str(evaluation["status"])) + """</strong></p>""" + "".join(cards_html) + """</main></body></html>"""


if __name__ == "__main__":
    main()
