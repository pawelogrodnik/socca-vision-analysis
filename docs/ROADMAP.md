# Roadmap

## Dokumenty kanoniczne

Globalna kolejność produktu znajduje się tutaj.

Szczegółowy player identity plan:

```text
task-requests/PLAYER_IDENTITY_DEVELOPMENT_PLAN.md
```

Operator flow:

```text
task-requests/PLAYER_IDENTITY_AUTOMATION_FLOW.md
```

Production safety/apply:

```text
task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
```

---

# Stan fundamentu

## Platforma

- [x] React/Vite client
- [x] FastAPI backend
- [x] Docker Compose
- [x] local-analysis / production-viewer separation
- [x] SQLite published-match store
- [x] local/remote publish contracts

## Analiza wideo

- [x] upload video
- [x] pitch calibration
- [x] YOLO player adapter
- [x] raw tracking artifacts
- [x] pitch-position mapping
- [x] overlay preview generation
- [x] browser-compatible H.264 output through FFmpeg

## Player identity foundations

- [x] tracklet splitting
- [x] candidate/stable subjects
- [x] team candidates
- [x] Initial Identity Audit
- [x] operator seed store and telemetry
- [x] seed-aware review reduction
- [x] whole-subject review
- [x] promotion safety audit
- [x] structural remediation
- [x] partial candidate identity
- [x] candidate timeline/stats/heatmaps
- [x] appearance/ReID advisory infrastructure
- [x] Match Identity Resolver shadow contract

## Ball/event foundations

- [x] ball detector/tracking foundations
- [x] possession candidate foundations
- [x] contact/pass/event candidate artifacts
- [ ] real-match quality closeout for possession and passes

---

# Current Product MVP — Reviewed Match Output

Najbliższy cel:

```text
analysis
→ short identity review
→ finalize reviewed identity
→ reviewed video
→ minimap
→ basic stats with coverage
```

## MVP 1 — Canonical reviewed identity

- [x] create `reviewed_identity_snapshot.json`
- [x] combine Initial Audit and whole-subject decisions
- [x] preserve explicit unresolved/conflicted states
- [x] generate stable Axx/Bxx fallback labels
- [x] make reviewed snapshot the single input for exports/stats
- [x] keep production identity unchanged

## MVP 2 — Reviewed video

- [x] add `Generate reviewed video` action
- [x] render confirmed roster names only
- [x] render Axx/Bxx for probable/unresolved/conflicted tracklets
- [x] include team colors and optional conflict marker
- [x] persist snapshot/source digests in a video manifest
- [x] support downstream-only rerender after corrections

## MVP 3 — Minimap/radar

- [x] draw Team A and Team B positions on a small pitch
- [x] add ball marker when ball position is available
- [ ] optionally show confirmed initials/number
- [ ] smooth marker motion conservatively
- [ ] use the same pitch orientation as heatmaps

## MVP 4 — Reviewed player stats

- [x] reviewed timeline
- [x] playing/detected time with unknown playing-time denominator
- [x] player heatmaps
- [x] average position
- [x] observed distance with experimental readiness status
- [ ] team shape diagnostics
- [ ] possession/pass attribution for confirmed identity windows
- [x] per-feature coverage and readiness

## MVP 5 — Video-driven correction

- [x] timestamp lookup for active reviewed tracklets/subjects
- [x] correct assignment or mark unresolved
- [x] invalidate reviewed snapshot by digest
- [x] rebuild snapshot/video/stats without YOLO or tracking rerun

---

# Automation Track

Automation work may proceed in parallel only when it does not delay the reviewed MVP.

## A1 — Final bounded ReID evaluation

- [x] training/evaluation infrastructure
- [x] same-team ranking and tracklet-level protocol
- [x] staged OSNet fine-tuning code
- [ ] real pretrained full-body run
- [ ] real fine-tuned full-body run
- [ ] real fine-tuned torso run
- [ ] H1-only winner freeze
- [ ] one final H2 replay
- [ ] product decision based on manual work saved and false-merge impact

## A2 — Resolver integration

- [x] shadow evidence/constraint resolver contract
- [ ] consume final review decisions as canonical anchors
- [ ] expose resolver suggestions inside one shared review flow
- [ ] prevent a second competing final identity pipeline
- [ ] measure Resolver A/B/C against operator workload and identity stability

## A3 — Exception-only review

- [ ] prioritize hard conflicts
- [ ] possible ID-switch boundaries
- [ ] long unresolved intervals
- [ ] possible substitutions/new players
- [ ] fragments with high stats impact
- [ ] operator/ReID/jersey conflicts

## A4 — Adaptive audit

- [ ] use telemetry from real reviewed exports
- [ ] choose next frame by expected information gain
- [ ] stop when further safe coverage gain is negligible

## A5 — Full-match benchmark

- [ ] evaluate more than one physical match
- [ ] include held-out material
- [ ] measure operator time and decisions
- [ ] measure confirmed/unresolved coverage
- [ ] audit final reviewed video errors
- [ ] measure ID switches, false merges and false splits

---

# Production Apply Track

Production apply is not required for the first local reviewed MVP.

## P1 — Reviewed revalidation

- [ ] 0 known false names after final reviewed-video correction
- [ ] 0 cross-team confirmed assignments
- [ ] 0 impossible parallel confirmed assignments
- [ ] stats deltas explainable through source decisions
- [ ] feature readiness recalculated

## P2 — Controlled production apply

- [ ] explicit confirmation UX
- [ ] candidate/reviewed vs production diff
- [ ] backup files
- [ ] transaction manifest
- [ ] atomic writes
- [ ] downstream rebuild
- [ ] post-apply validation
- [ ] rollback

No automatic production apply.

---

# Later Features

## Tracking quality

- [ ] benchmark BoT-SORT vs ByteTrack on representative footage
- [ ] improve missed/merged player detection based on measured QA findings
- [ ] targeted detector training only when benchmark evidence justifies it

## Advanced identity

- [ ] substitution/new-player assistance
- [ ] advanced event-level/orphan review only for proven high-impact cases
- [ ] persistent cross-match gallery only after stable single-match reviewed flow
- [ ] jersey recognition only after new data/readiness evidence

## Football analytics

- [ ] speed and sprint thresholds
- [ ] team compactness/width/depth
- [ ] formations and shape changes
- [ ] progressive passes
- [ ] turnovers
- [ ] shots and shot locations
- [ ] tactical key moments
- [ ] coaching highlights

---

# Product success definition

The first useful product does not require perfect automatic identity.

It requires:

```text
few high-value manual decisions
confirmed names that are correct
Axx/Bxx where identity is uncertain
reviewed video that exposes mistakes
minimap and basic stats
explicit coverage/readiness
cheap correction and rerender
```

Automation is successful only when it reduces operator work without increasing false assignments.
