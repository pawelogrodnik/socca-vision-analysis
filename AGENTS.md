# AGENTS.md — Orlik Vision App

These instructions apply to the whole repository unless a nested `AGENTS.md` overrides them.

## Product context

Orlik Vision is a local-first video analysis app for amateur 7v7 football/orlik matches. The current goal is not a perfect Opta-like system. The first reliable product layer is:

1. upload match video,
2. calibrate pitch,
3. detect and track players,
4. review tracker ID flickering,
5. later resolve `tracklet -> player_id -> stint`,
6. generate player/team stats such as play time, heatmaps, distance and sprints.

Treat raw `tracker_id` as a temporary computer-vision identifier, not a real player identity. Real player identity must be represented separately as `player_id` and connected through assignments/stints.

## Repository layout

```text
client/   React + Vite + TypeScript UI
backend/  FastAPI + Python video analysis API
docs/     architecture, roadmap, data model notes
examples/ small local demo assets
```

## Engineering principles

- Prefer small, composable modules over large files.
- Keep UI, domain logic, API calls and data transformations separate.
- Avoid duplicating logic between client and backend; define contracts clearly.
- Keep the current MVP simple: manual pitch calibration first, then semi-auto later.
- Do not introduce heavy infrastructure unless it solves an immediate project need.
- Make changes easy to test on short video clips before running full-match analysis.
- Preserve generated artifacts as files under match storage; do not hide important results only in memory.
- Be explicit about confidence and uncertainty in CV outputs.

## Data model rules

Use these concepts consistently:

- `Match`: one uploaded match/video.
- `PitchConfig`: image points, pitch dimensions and calibration source.
- `Detection`: one raw object detection in one frame.
- `Tracklet`: continuous tracker output segment.
- `Player`: real-world person in a team roster.
- `Stint`: interval when a player is on pitch.
- `IdentityAssignment`: mapping from tracklet(s) to player/stint.

Do not collapse `track_id`, `tracklet_id` and `player_id` into one concept.

## Generated files and storage

- Keep user-uploaded videos and generated artifacts out of git.
- Store per-match outputs in `backend/storage/matches/<match_id>/`.
- Prefer stable JSON contracts for MVP outputs:
  - `match.json`
  - `pitch_config.json`
  - `tracks.json`
  - `analysis_report.json`
- Later, large tabular outputs may move to parquet/SQLite/Postgres, but do not prematurely migrate.

## Coding style

- Use descriptive names. Avoid abbreviations except common CV terms such as `fps`, `bbox`, `iou`.
- Add comments only when they explain non-obvious decisions, not every line.
- Prefer pure helper functions for transformations and calculations.
- Validate inputs near API boundaries.
- Keep long-running video analysis isolated from request/response logic; the current synchronous endpoint is MVP-only.
- Na froncie każdy nowy komponent pisz w React i dbaj o minimalny zakres odpowiedzialności: logikę wynoś do osobnych plików `utils/`, `types/`, `consts/`, a komponent trzymaj w dedykowanym pliku `.tsx`.
- Na backendzie utrzymuj ten sam modularny podział – rozbijaj rozrastające się pliki na mniejsze moduły/serwisy i dodawaj testy jednostkowe do nowych utili oraz scraperów na bieżąco.
  Po każdej większej zmianie uruchom `npx tsc --noEmit --noUnusedLocals --noUnusedParameters` osobno w `client/`. Usuń wszystkie wskazane importy i parametry zanim zgłosisz pracę.
  - do nawigacji po stronie frontendowej uzywamy routera, nie robimy zadnych workaroundow - aplikacja od poczatku ma byc pisania zgodnie z najlepszymi standardami

## Before adding a feature

Ask where it belongs:

- UI/interaction only -> `client/`
- API contract or orchestration -> `backend/app/main.py` or routers/services
- CV/video processing -> `backend/app/services/`
- domain/stat calculation -> backend domain/stat modules, not FastAPI handlers
- product/architecture notes -> `docs/`

## MVP scope guardrails

In early iterations, avoid implementing these as core requirements:

- automatic jersey number recognition,
- face recognition,
- fully automatic pitch calibration with no manual correction,
- fully automatic pass/shot/event classification,
- complex auth/multi-tenant user management,
- cloud deployment assumptions.

Build the reliable tracking/stat foundation first.

## Progressive implementation plan

When implementing new features, follow `docs/IMPLEMENTATION_PLAN.md`. It defines milestone order, user stories, acceptance criteria, and explicit scope boundaries. Do not skip ahead to ball/event analytics before tracking, tracklets, identity assignments and tracking-only player stats are usable.

## UTF-8 editing notes for docs

- `docs/IMPLEMENTATION_PLAN.md` is UTF-8. In Windows PowerShell, always read it with an explicit encoding:
  - `Get-Content docs/IMPLEMENTATION_PLAN.md -Encoding utf8`
- Do not trust mojibake shown by plain `Get-Content docs/IMPLEMENTATION_PLAN.md`; sequences like `â€”`, `Ĺ‚`, `Ä…` or `Ăł` can be a console decoding issue, not real file corruption.
- Prefer `apply_patch` for manual edits. If a shell/editor must write the file, make sure it writes UTF-8 and does not convert the file to ANSI/Windows-1250.
- Before editing, verify that the file can be decoded as UTF-8:
  - `python -c "from pathlib import Path; Path('docs/IMPLEMENTATION_PLAN.md').read_text(encoding='utf-8'); print('utf8 ok')"`
- After editing, re-open with `Get-Content ... -Encoding utf8` and run the corrupted-character check below.

## Validation before save

- Check that no corrupted characters were introduced outside explicit encoding examples in this section, including:
  - `�`
  - `ï¿½`
  - `Ã³`
  - `Å‚`
  - `Ä…`
  - `Ä‡`
  - `Ĺ`
  - `Ăł`
  - `â€”`
- If any such sequence appears in changed content where it is not intentionally quoted as an encoding/mojibake example, abort the change and restore the previous content.

## Safety rule

- If encoding is unclear or file content looks corrupted, do not edit the file.
- Report the issue instead of saving changes.
