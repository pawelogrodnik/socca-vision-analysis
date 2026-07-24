# AGENTS.md — Orlik Vision App

These instructions apply to the whole repository unless a nested `AGENTS.md` overrides them.

## Product context

Orlik Vision is a local-first video analysis app for amateur 7v7 football/orlik matches. The current goal is not a perfect Opta-like system. The first reliable product layer is:

1. upload match video,
2. calibrate pitch,
3. detect and track players,
4. run a quick operator identity-seed audit on a few automatically selected frames,
5. resolve `tracklet -> player_id -> stint`,
6. review only unresolved/conflicting subjects and tracker ID flickering,
7. generate player/team stats such as play time, heatmaps, distance and sprints.

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

## Mandatory human-audit and operator UX contract

The operator is a football user who knows the players. The operator is **not** an annotation worker, computer-vision engineer, JSON editor or coordinate calculator. Every audit/review feature must therefore be designed as a fast, obvious product interaction.

### Core rule

```text
user supplies human knowledge
application supplies all technical metadata
```

The user may know:

```text
this is Roman #6 from Team A
this is definitely an unknown Team B player
this detection is a referee
this detection is false
I am not sure, so I will skip it
```

The application/agent must derive or persist automatically:

```text
x1 / y1 / x2 / y2
normalized coordinates
frame and timestamp
track_id / tracklet_id / subject_id
artifact paths and hashes
digests and schema versions
model confidence and thresholds
capture-domain metadata
lineage/provenance fields
```

Never require the operator to calculate, copy or type those technical values. When geometry is needed, provide click/drag/draw interaction and compute coordinates internally. When a frame, crop, tracklet or artifact is needed, the system must select and display it. When confidence is needed internally, compute it from model/lineage evidence; do not ask the user for a percentage.

Existing developer/debug forms that expose raw coordinate fields may remain temporarily, but they are technical debt. Do not copy that interaction into new operator workflows. When such a form is materially changed, prefer replacing it with direct manipulation such as drawing a box on the image.

### Audits must be quick actions, not labeling projects

Do not design a normal product audit that requires reviewing or annotating hundreds of crops. Do not make exhaustive completion a prerequisite when a small set of high-value confirmations is enough.

For every new audit/review flow:

- automatically prioritize the smallest set of examples with the highest expected information gain;
- prefer clear, non-overlapping, high-confidence frames and crops;
- hide low-value noise unless it affects identity, safety or final statistics;
- allow `Skip / Nie wiem` without penalty;
- save after every action and allow resume;
- provide an obvious `Finish audit` action even when some items remain unresolved;
- keep unresolved data explicit instead of forcing a guess;
- measure active operator time and number of actions;
- report whether the audit reduced later review work.

A larger dataset may be required for model research, but that is a separate offline/admin workflow. It must not be silently converted into a mandatory end-user task.

### Certainty semantics

The fast operator audit uses a deliberately simple contract:

```text
certain assignment
or
skip / unknown
```

Do not ask the user for `60%`, `medium confidence`, IoU quality, blur score, visibility score or similar technical judgments. A selected named player is an operator-certain observation-level anchor. No selection means no anchor.

Useful one-click actions should include:

```text
named roster player (implies team and roster number)
Team A — player unknown
Team B — player unknown
referee
false detection
skip / not sure
```

Selecting `Roman #6 · Team A` must automatically correct an incorrect automatic Team B assignment for that observation and record the contradiction. Do not ask the user to separately edit the team.

### Initial Identity Audit MVP

The first player-identity audit must be a short stage after automatic tracking/stabilization and before the existing whole-subject review:

```text
automatic analysis
→ quick Initial Identity Audit
→ seed-aware downstream identity re-resolve without rerunning YOLO
→ optional short second-half re-anchor
→ existing whole-subject review only for unresolved/conflicting cases
→ candidate timeline and stats validation
```

MVP interaction budget:

```text
5–8 automatically selected frames by default
10 frames maximum unless the operator explicitly asks for more
roughly 8–12 certain assignments as a target, not a requirement
stop early when no new easy/high-value player is available
```

Frame selection must optimize for:

```text
many visible players
new not-yet-seeded players
low bbox overlap
large enough player crops
high detection quality
tracklet continuity around the selected frame
few edge-cut players
low motion blur
capture-domain diversity
```

Do not show ten nearly identical frames. Each next frame should ideally reveal a new player or resolve a previous ambiguity. A small second-half re-anchor may use 2–3 frames because the camera side, angle and lighting change.

The GUI should support both fast directions:

```text
click bbox → choose player/action
click roster player → click bbox
```

Clicking a bbox should show the full-frame context plus a readable crop. A lightweight neighboring-frame strip may be used to catch a local ID switch, but the user must not be forced into a long clip-by-clip review.

The audit creates observation-level seeds. It must not blindly assign a name to the entire raw tracker ID. Propagation to a tracklet, stable subject or other fragments must pass existing lineage, temporal-overlap, team and structural safety checks.

The first MVP does **not** require:

```text
named MP4 generation
manual correction of every missed detection
full timeline editor
automatic substitution review
names for opponent players
retraining a model during the audit
exhaustive jersey-number annotation
```

### Flow integration requirement

A new audit is valuable only when it shortens the later workflow. Do not add a new mandatory screen and then require the operator to repeat the same assignments in whole-subject review.

After saving seeds:

- rebuild only downstream identity/candidate artifacts from frozen detections/tracks;
- do not rerun full-match YOLO;
- carry operator-confirmed player/team information into subject recommendations;
- automatically hide or mark already-resolved review cards;
- surface contradictions, unresolved fragments and possible ID switches;
- retain provenance from frame observation to tracklet, subject and candidate player assignment;
- leave production identity unchanged until the roadmap explicitly permits promotion.

Minimum effectiveness telemetry:

```text
audit_frames_shown
audit_actions
active_operator_seconds
unique_players_seeded
team_assignments_corrected
tracklets_seeded
subjects_resolved_after_seeding
review_cards_before_seeding
review_cards_after_seeding
conflicts_created
```

The feature is not successful merely because the UI exists. It must demonstrate fewer later review actions or better safe identity coverage.

### Agent behavior when human input is unavailable

Do not ask the user for information they cannot reasonably provide, such as internal IDs, exact frame coordinates, digest values, model confidence or a hand-built list of hundreds of samples.

When a task appears to need such input, the agent must first choose one of these approaches:

1. derive it automatically from current artifacts;
2. build an intuitive selector/drawing/picker UI;
3. generate a small curated review package with one-click choices;
4. reduce the experiment scope and state what can be proved with available data;
5. stop safely and report the missing machine-generated prerequisite.

Asking the operator to behave like a script is not an acceptable workaround.

### UX acceptance checklist for every audit feature

Before marking an audit/review milestone complete, verify:

- a user can understand the next action without reading implementation docs;
- no mandatory raw coordinates, hashes, IDs or numeric confidence fields are exposed;
- the default path requires only high-value human decisions;
- `Skip / Nie wiem` is always available;
- progress and remaining work are visible;
- the flow is resumable and does not lose prior decisions;
- the user can finish without resolving every item;
- technical details are hidden under an optional developer/debug view;
- keyboard shortcuts or one-click actions are used where repeated choices exist;
- the audit has an explicit human-effort budget and telemetry;
- downstream review is measurably reduced or better prioritized;
- tests cover the user-facing contract, not only JSON schemas.

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

## Current YOLO model defaults

Keep the default analysis pipeline aligned with the current local models:

- Player detector: `models/best-model-with-ball-and-players-500-frames.pt`
- Ball detector: `models/best-balls-only-800-frames.pt`

These are paths resolved from `backend/models/`. When moving work to another laptop, copy `backend/models/` or restore these `.pt` files before running analysis. Treat `yolov8n.pt` and `models/best.pt` as legacy/fallback comparison models unless the user explicitly asks to benchmark them.

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