from __future__ import annotations

"""Render a small private, human-readable review package for contact paths.

This is an evaluation-only utility.  It reads persisted reviewed video and
ball evidence, never runs inference, and writes only under storage/benchmarks.
"""

import argparse
import html
import json
from pathlib import Path
from typing import Any, Mapping

from app.config import STORAGE_DIR
from app.services.reviewed_ball_diagnostic import render_reviewed_ball_diagnostic


DEFAULT_ANALYSIS = (
    STORAGE_DIR
    / "benchmarks"
    / "ball-continuity-root-cause-v1"
    / "corgi-verisk"
    / "ball_continuity_failure_analysis.json"
)
DEFAULT_OUTPUT = DEFAULT_ANALYSIS.parent / "operator-contact-review"
DEFAULT_TIMESTAMPS = ("10:54", "16:15", "17:04", "18:52")


def build_operator_review_cases(
    analysis: Mapping[str, Any],
    timestamps: tuple[str, ...] = DEFAULT_TIMESTAMPS,
) -> list[dict[str, Any]]:
    """Return team-consistent contact choices, labelled for a football operator."""

    wanted = set(timestamps)
    cases: list[dict[str, Any]] = []
    for row in analysis.get("cases") or []:
        if not isinstance(row, Mapping):
            continue
        display = str(row.get("timestamp_display") or "").rstrip("~")
        if display not in wanted:
            continue
        choices: list[dict[str, Any]] = []
        for index, path in enumerate(row.get("path_diagnoses") or []):
            if not isinstance(path, Mapping):
                continue
            contact = path.get("contact_path")
            if not isinstance(contact, Mapping):
                continue
            letter = chr(ord("A") + index)
            player = str(contact.get("player") or "zawodnik nieustalony")
            team = str(contact.get("team") or "drużyna nieustalona")
            launch_sec = float(contact.get("timestamp_sec") or 0.0)
            choices.append(
                {
                    "letter": letter,
                    "team": team,
                    "player": player,
                    "launch_sec": launch_sec,
                    "label": f"Opcja {letter} — {team}",
                }
            )
        if choices:
            cases.append(
                {
                    "timestamp_display": display,
                    "source_match_id": str(row.get("source_match_id") or ""),
                    "choices": choices,
                }
            )
    return sorted(cases, key=lambda item: timestamps.index(str(item["timestamp_display"])))


def _write_index(output: Path, cases: list[dict[str, Any]]) -> None:
    sections: list[str] = []
    for case in cases:
        case_key = str(case["timestamp_display"]).replace(":", "-")
        options: list[str] = []
        for choice in case["choices"]:
            clip_name = str(choice["clip_name"])
            letter = str(choice["letter"])
            label = html.escape(str(choice["label"]))
            options.append(
                f"""
                <article class=\"option\">
                  <h3>{label}</h3>
                  <video controls preload=\"metadata\" src=\"{html.escape(clip_name)}\"></video>
                  <button type=\"button\" data-case=\"{case['timestamp_display']}\" data-answer-id=\"{case_key}\" data-choice=\"{letter}\">To jest faktyczne uderzenie</button>
                </article>"""
            )
        case_time = html.escape(str(case["timestamp_display"]))
        sections.append(
            f"<section><h2>Sprawdzany moment: {case_time}</h2><p>Wybierz jeden klip, na którym widzisz faktyczne uderzenie piłki. Jeśli żaden albo nie masz pewności, wybierz „Nie wiem”.</p><div class=\"options\">{''.join(options)}</div><button class=\"unknown\" type=\"button\" data-case=\"{case_time}\" data-answer-id=\"{case_key}\" data-choice=\"nie wiem\">Nie wiem / żaden</button><p class=\"answer\" id=\"answer-{case_key}\" data-case=\"{case_time}\"></p></section>"
        )
    document = f"""<!doctype html>
<html lang=\"pl\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<title>Weryfikacja kontaktów piłki</title>
<style>body{{font:18px/1.45 system-ui,sans-serif;max-width:1200px;margin:32px auto;padding:0 20px;color:#eaf4ff;background:#06182b}}h1,h2,h3{{color:#fff}}section{{border-top:1px solid #31516e;padding:22px 0}}.options{{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:18px}}.option{{background:#0a263e;border:1px solid #31516e;border-radius:12px;padding:14px}}video{{width:100%;background:#000;border-radius:8px}}button{{font:inherit;font-weight:700;border:0;border-radius:8px;padding:10px 14px;background:#26c96a;color:#062215;cursor:pointer}}.unknown{{background:#d8e4ec;color:#132331;margin-top:16px}}.answer{{font-weight:700;color:#6ee7a1}}</style>
<h1>Weryfikacja kontaktów piłki</h1><p>To są krótkie wycinki z istniejącego reviewed video. Żaden model nie był ponownie uruchamiany. Nakładki pokazują zawodników i zapisaną pozycję piłki; pomarańczowy komunikat wskazuje rozważany kontakt.</p>{''.join(sections)}
<script>document.querySelectorAll('button[data-case]').forEach(button=>button.addEventListener('click',()=>{{const key=button.dataset.case;const answer=button.dataset.choice;localStorage.setItem('contact-review-'+key,answer);document.getElementById('answer-'+button.dataset.answerId).textContent='Wybrano: '+answer.toUpperCase()+'. Prześlij mi tę odpowiedź.';}}));document.querySelectorAll('.answer[data-case]').forEach(node=>{{const answer=localStorage.getItem('contact-review-'+node.dataset.case);if(answer)node.textContent='Wybrano wcześniej: '+answer.toUpperCase()+'.';}});</script>"""
    (output / "index.html").write_text(document, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--analysis", type=Path, default=DEFAULT_ANALYSIS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--before-sec", type=float, default=4.0)
    parser.add_argument("--after-sec", type=float, default=4.0)
    args = parser.parse_args()
    output = args.output.resolve()
    benchmark_root = (STORAGE_DIR / "benchmarks").resolve()
    if benchmark_root not in output.parents:
        raise ValueError("operator_review_output_must_be_under_storage_benchmarks")
    if args.before_sec <= 0 or args.after_sec <= 0:
        raise ValueError("operator_review_window_must_be_positive")

    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    cases = build_operator_review_cases(analysis)
    if not cases:
        raise RuntimeError("operator_review_cases_not_found")
    output.mkdir(parents=True, exist_ok=True)
    for case in cases:
        match_path = STORAGE_DIR / "matches" / case["source_match_id"]
        reviewed_video = match_path / "reviewed_video.mp4"
        ball_tracks = json.loads((match_path / "ball_tracks.json").read_text(encoding="utf-8"))
        for choice in case["choices"]:
            stem = f"{case['timestamp_display'].replace(':', '-')}-opcja-{choice['letter'].lower()}"
            clip = output / f"{stem}.mp4"
            choice["clip_name"] = clip.name
            render_reviewed_ball_diagnostic(
                reviewed_video,
                ball_tracks,
                clip,
                start_sec=max(0.0, float(choice["launch_sec"]) - args.before_sec),
                end_sec=float(choice["launch_sec"]) + args.after_sec,
                operator_title=str(choice["label"]),
                highlight_time_sec=float(choice["launch_sec"]),
            )
    _write_index(output, cases)
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "kind": "private_contact_path_operator_review:v1",
                "evaluation_only": True,
                "inference_invoked": False,
                "window": {"before_sec": args.before_sec, "after_sec": args.after_sec},
                "cases": cases,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(output), "clips": sum(len(case["choices"]) for case in cases)}))


if __name__ == "__main__":
    main()
