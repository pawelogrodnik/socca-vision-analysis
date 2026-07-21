#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from app.services.identity_operator_benchmark import build_identity_operator_benchmark
from app.services.resolved_player_timeline import build_resolved_player_timeline_from_files


PRODUCTION_COLOR = (0, 145, 255)
CANDIDATE_COLOR = (255, 190, 20)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build P1.22 production-vs-candidate operator benchmark.")
    parser.add_argument("--match-dir", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--held-out", action="store_true")
    parser.add_argument("--video", type=Path)
    parser.add_argument("--promotion-plan", type=Path)
    parser.add_argument("--review-decisions", type=Path)
    parser.add_argument("--start-sec", type=float, default=0.0)
    parser.add_argument("--max-seconds", type=float, default=0.0)
    parser.add_argument("--max-gallery-cards", type=int, default=240)
    parser.add_argument("--render-video", action="store_true")
    args = parser.parse_args()

    match_doc = _load(args.match_dir / "match.json")
    production = build_resolved_player_timeline_from_files(args.match_dir)
    candidate = _load(args.candidate_root / "resolved_player_timeline_candidate_v2.json")
    manifest = _load_optional(args.candidate_root / "identity_candidate_apply_manifest.json")
    promotion = _load_optional(args.promotion_plan) if args.promotion_plan else _find_promotion_plan(args.candidate_root)
    review = _load_optional(args.review_decisions) if args.review_decisions else _load_optional(
        args.match_dir / "identity_roster_subject_review_decisions_shadow.json"
    )
    benchmark = build_identity_operator_benchmark(
        production_timeline=production,
        candidate_timeline=candidate,
        match_doc=match_doc,
        candidate_manifest=manifest,
        promotion_plan=promotion,
        review_decisions=review,
        label=args.label,
        held_out=args.held_out,
        start_sec=max(0.0, args.start_sec),
        max_seconds=max(0.0, args.max_seconds),
    )
    args.output_root.mkdir(parents=True, exist_ok=True)
    _write(args.output_root / "identity_operator_benchmark.json", benchmark)

    video = args.video or args.match_dir / "video.mp4"
    rendered = 0
    if video.exists():
        rendered = _render_gallery(video, benchmark, args.output_root, args.max_gallery_cards)
        if args.render_video:
            _render_comparison_video(
                video,
                production,
                candidate,
                match_doc,
                args.output_root / "identity_production_vs_candidate.mp4",
                start_frame=int(benchmark["benchmark"]["start_frame"]),
                end_frame=benchmark["benchmark"]["end_frame"],
            )
    _write_index(args.output_root, benchmark, rendered)
    (args.output_root / "P1_22_OPERATOR_BENCHMARK.md").write_text(
        _markdown_report(benchmark, rendered),
        encoding="utf-8",
    )
    print(json.dumps({
        "output_root": str(args.output_root),
        "cards": len(benchmark["cards"]),
        "human_review_cards": benchmark["metrics"]["human_review_cards"],
        "gallery_cards_rendered": rendered,
        "comparison_video": args.render_video and video.exists(),
    }, indent=2))
    return 0


def _render_gallery(video: Path, benchmark: dict[str, Any], output: Path, maximum: int) -> int:
    cards = sorted(
        [card for card in benchmark.get("cards") or [] if card.get("requires_human_review")],
        key=lambda card: (_severity_rank(str(card.get("severity"))), int(card.get("sample_frame") or 0)),
    )[: max(0, maximum)]
    images = output / "cards"
    images.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video))
    candidate_safety = benchmark.get("benchmark", {}).get("mode") == "candidate_safety_audit"
    rendered = 0
    try:
        for index, card in enumerate(cards, start=1):
            before_after = (card.get("evidence") or {}).get("comparison_semantics") == "candidate_before_after"
            left_frame = _read_video_frame(
                capture,
                int(card["start_frame"] if before_after else card["sample_frame"]),
            )
            right_frame = _read_video_frame(
                capture,
                int(card["end_frame"] if before_after else card["sample_frame"]),
            )
            if left_frame is None or right_frame is None:
                card["image_artifact"] = None
                card["render_error"] = "video_frame_unavailable"
                continue
            composite = _comparison_card(left_frame, right_frame, card, candidate_safety=candidate_safety)
            name = f"{index:04d}_{card['category']}_f{int(card['sample_frame']):06d}.jpg"
            cv2.imwrite(str(images / name), composite, [cv2.IMWRITE_JPEG_QUALITY, 90])
            card["image_artifact"] = f"cards/{name}"
            rendered += 1
    finally:
        capture.release()
    _write(output / "identity_operator_benchmark.json", benchmark)
    return rendered


def _read_video_frame(capture: cv2.VideoCapture, frame_index: int) -> np.ndarray | None:
    capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_index))
    ok, frame = capture.read()
    return frame if ok else None


def _comparison_card(
    left_frame: np.ndarray,
    right_frame: np.ndarray,
    card: dict[str, Any],
    *,
    candidate_safety: bool,
) -> np.ndarray:
    left = left_frame.copy()
    right = right_frame.copy()
    _draw_observation(left, card.get("production"), card["player_name"], PRODUCTION_COLOR)
    _draw_observation(right, card.get("candidate"), card["player_name"], CANDIDATE_COLOR)
    pane_width = 840
    left_height, left_width = left.shape[:2]
    right_height, right_width = right.shape[:2]
    pane_height = max(
        1,
        int(max(left_height / left_width, right_height / right_width) * pane_width),
    )
    left = cv2.resize(left, (pane_width, pane_height), interpolation=cv2.INTER_AREA)
    right = cv2.resize(right, (pane_width, pane_height), interpolation=cv2.INTER_AREA)
    header = 52
    footer = 120
    canvas = np.full((pane_height + header + footer, pane_width * 2, 3), (16, 24, 39), dtype=np.uint8)
    canvas[header:header + pane_height, :pane_width] = left
    canvas[header:header + pane_height, pane_width:] = right
    before_after = (card.get("evidence") or {}).get("comparison_semantics") == "candidate_before_after"
    left_label = "CANDIDATE BEFORE" if before_after else "PRODUCTION"
    right_label = "CANDIDATE AFTER" if before_after else "CANDIDATE"
    _text(canvas, left_label, (24, 35), PRODUCTION_COLOR, 0.9, 2)
    _text(canvas, right_label, (pane_width + 24, 35), CANDIDATE_COLOR, 0.9, 2)
    line_y = header + pane_height + 34
    _text(canvas, f"{card['player_name']}  |  {card['category']}  |  frame {card['sample_frame']}", (24, line_y), (240, 244, 250), 0.78, 2)
    evidence = json.dumps(card.get("evidence") or {}, sort_keys=True, ensure_ascii=True)
    _text(canvas, evidence[:180], (24, line_y + 38), (175, 188, 207), 0.58, 1)
    instruction = (
        "Decide: candidate correct / candidate wrong / unclear"
        if candidate_safety
        else "Decide: candidate / production / both wrong / unclear"
    )
    _text(canvas, instruction, (24, line_y + 74), (120, 210, 160), 0.62, 1)
    return canvas


def _draw_observation(frame: np.ndarray, observation: Any, player_name: str, color: tuple[int, int, int]) -> None:
    if not isinstance(observation, dict):
        _text(frame, "NO OBSERVATION", (30, 70), color, 1.2, 3)
        return
    bbox = observation.get("bbox_xyxy")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        x1, y1, x2, y2 = (int(round(float(value))) for value in bbox[:4])
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 4)
        _text(frame, player_name, (x1, max(32, y1 - 10)), color, 0.85, 3)


def _render_comparison_video(
    video: Path,
    production: dict[str, Any],
    candidate: dict[str, Any],
    match_doc: dict[str, Any],
    output: Path,
    *,
    start_frame: int = 0,
    end_frame: int | None = None,
) -> None:
    production_frames = _timeline_frame_map(production, candidate=False)
    candidate_frames = _timeline_frame_map(candidate, candidate=True)
    roster = _roster(match_doc)
    capture = cv2.VideoCapture(str(video))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    pane_width = width // 2
    pane_height = max(1, int(height * pane_width / width))
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, pane_height + 42))
    frame_index = max(0, start_frame)
    capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    try:
        while True:
            if end_frame is not None and frame_index > end_frame:
                break
            ok, frame = capture.read()
            if not ok:
                break
            left, right = frame.copy(), frame.copy()
            for player_id, row in production_frames.get(frame_index, []):
                _draw_observation(left, row, roster.get(player_id, player_id), PRODUCTION_COLOR)
            for player_id, row in candidate_frames.get(frame_index, []):
                _draw_observation(right, row, roster.get(player_id, player_id), CANDIDATE_COLOR)
            left = cv2.resize(left, (pane_width, pane_height), interpolation=cv2.INTER_AREA)
            right = cv2.resize(right, (width - pane_width, pane_height), interpolation=cv2.INTER_AREA)
            canvas = np.full((pane_height + 42, width, 3), (16, 24, 39), dtype=np.uint8)
            canvas[42:, :pane_width] = left
            canvas[42:, pane_width:] = right
            _text(canvas, "PRODUCTION", (18, 30), PRODUCTION_COLOR, 0.75, 2)
            _text(canvas, "CANDIDATE", (pane_width + 18, 30), CANDIDATE_COLOR, 0.75, 2)
            writer.write(canvas)
            frame_index += 1
    finally:
        capture.release()
        writer.release()


def _timeline_frame_map(document: dict[str, Any], *, candidate: bool) -> dict[int, list[tuple[str, dict[str, Any]]]]:
    result: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    players = document.get("players") or ([] if candidate else {})
    iterable = ((str(row.get("player_id") or ""), row.get("observations") or []) for row in players) if candidate else ((str(key), value.get("rows") or []) for key, value in players.items())
    for player_id, rows in iterable:
        for row in rows:
            if isinstance(row, dict) and row.get("frame") is not None:
                result.setdefault(int(row["frame"]), []).append((player_id, row))
    return result


def _write_index(root: Path, benchmark: dict[str, Any], rendered: int) -> None:
    cards_json = json.dumps([
        {
            "card_key": card["card_key"],
            "category": card["category"],
            "severity": card["severity"],
            "player_name": card["player_name"],
            "sample_frame": card["sample_frame"],
            "image_artifact": card.get("image_artifact"),
            "evidence": card.get("evidence"),
        }
        for card in benchmark.get("cards") or []
        if card.get("image_artifact")
    ], ensure_ascii=False).replace("</", "<\\/")
    decision_labels = (
        {
            "candidate_correct": "Candidate poprawny",
            "candidate_wrong": "Candidate bledny",
            "unclear": "Niejasne",
        }
        if benchmark.get("benchmark", {}).get("mode") == "candidate_safety_audit"
        else {
            "prefer_candidate": "Candidate",
            "keep_production": "Production",
            "both_wrong": "Oba bledne",
            "unclear": "Niejasne",
        }
    )
    labels_json = json.dumps(decision_labels, ensure_ascii=True)
    html = f"""<!doctype html>
<html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>P1.22 {benchmark['benchmark']['label']}</title>
<style>
body{{margin:0;background:#0b1220;color:#e8eef8;font:15px system-ui,sans-serif}}header{{position:sticky;top:0;background:#111b2e;padding:16px 24px;z-index:2;border-bottom:1px solid #31415e}}main{{padding:20px;display:grid;gap:18px}}.card{{border:1px solid #31415e;background:#111b2e;padding:14px;border-radius:6px}}img{{width:100%;display:block}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}button{{padding:9px 12px;background:#20314d;color:#fff;border:1px solid #4e6386;border-radius:5px;cursor:pointer}}button.active{{background:#168356;border-color:#55d39c}}.meta{{color:#aebbd0;margin:8px 0}}#download{{background:#168356}} 
</style></head><body><header><b>P1.22: {benchmark['benchmark']['label']}</b> · {rendered} kart
<button id="download">Pobierz audyt JSON</button></header><main id="cards"></main>
<script>const cards={cards_json};const decisions={{}};const labels={labels_json};
const root=document.getElementById('cards');cards.forEach((c,i)=>{{const el=document.createElement('section');el.className='card';el.innerHTML=`<h3>#${{i+1}} ${{c.player_name}} · ${{c.category}} · f${{c.sample_frame}}</h3><div class="meta">${{c.severity}} · ${{JSON.stringify(c.evidence)}}</div><img src="${{c.image_artifact}}"><div class="actions"></div>`;const actions=el.querySelector('.actions');Object.entries(labels).forEach(([key,label])=>{{const b=document.createElement('button');b.textContent=label;b.onclick=()=>{{decisions[c.card_key]=key;actions.querySelectorAll('button').forEach(x=>x.classList.remove('active'));b.classList.add('active')}};actions.appendChild(b)}});root.appendChild(el)}});
document.getElementById('download').onclick=()=>{{const blob=new Blob([JSON.stringify({{schema_version:'0.1.0',benchmark_label:'{benchmark['benchmark']['label']}',decisions}},null,2)],{{type:'application/json'}});const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='identity_operator_benchmark_reviewed_{benchmark['benchmark']['label']}.json';a.click();URL.revokeObjectURL(a.href)}};</script></body></html>"""
    (root / "index.html").write_text(html, encoding="utf-8")


def _find_promotion_plan(root: Path) -> dict[str, Any]:
    direct = root / "identity_roster_subject_promotion_plan.json"
    if direct.exists():
        return _load(direct)
    return {}


def _markdown_report(benchmark: dict[str, Any], rendered: int) -> str:
    metrics = benchmark.get("metrics") or {}
    distribution = (benchmark.get("coverage") or {}).get("distribution") or {}
    telemetry_note = (
        f"{float(metrics.get('manual_review_time_sec') or 0.0):.1f} s"
        if metrics.get("review_telemetry_available")
        else "unavailable for review completed before P1.22 telemetry"
    )
    return "\n".join([
        f"# P1.22 Operator Benchmark: {benchmark['benchmark']['label']}",
        "",
        f"- Held-out: `{str(bool(benchmark['benchmark'].get('held_out'))).lower()}`",
        f"- Mode: `{benchmark['benchmark'].get('mode') or 'production_vs_candidate'}`",
        f"- Production baseline: `{str(bool(benchmark['benchmark'].get('production_baseline_available', True))).lower()}`",
        f"- Duration: `{float(benchmark['benchmark'].get('duration_sec') or 0.0):.3f} s`",
        f"- Review time: `{telemetry_note}`",
        f"- Manual decisions: `{int(metrics.get('manual_decisions') or 0)}`",
        f"- Promoted detected ratio: `{_percent(metrics.get('promoted_detected_ratio'))}`",
        f"- Unresolved detected ratio: `{_percent(metrics.get('unresolved_detected_ratio'))}`",
        f"- Structural conflicts: `{int(metrics.get('structural_conflict_count') or 0)}`",
        f"- Parallel conflicts: `{int(metrics.get('parallel_conflict_count') or 0)}`",
        f"- Human review cards: `{int(metrics.get('human_review_cards') or 0)}`",
        f"- Rendered gallery cards: `{rendered}`",
        f"- Candidate full-video coverage median: `{_percent(distribution.get('median'))}`",
        "",
        "## Interpretation",
        "",
        "This benchmark never mutates production identity or public reports.",
        "When the production player timeline is unavailable, the gallery contains candidate safety risks only.",
        "A final P1.22 gate requires explicit review decisions for difference cards and at least three benchmark matches.",
        "",
    ])


def _percent(value: Any) -> str:
    return "n/a" if value is None else f"{100.0 * float(value):.2f}%"


def _roster(match_doc: dict[str, Any]) -> dict[str, str]:
    return {
        str(player["id"]): str(player.get("name") or player["id"])
        for team in match_doc.get("teams") or [] if isinstance(team, dict)
        for player in team.get("players") or [] if isinstance(player, dict) and player.get("id")
    }


def _severity_rank(value: str) -> int:
    return {"high": 0, "audit": 1, "medium": 2, "low": 3}.get(value, 4)


def _text(image: np.ndarray, value: str, origin: tuple[int, int], color: tuple[int, int, int], scale: float, thickness: int) -> None:
    cv2.putText(image, value, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 3, cv2.LINE_AA)
    cv2.putText(image, value, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_optional(path: Path) -> dict[str, Any]:
    return _load(path) if path.exists() else {}


def _write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
