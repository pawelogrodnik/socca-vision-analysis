# Local analysis admin vs production viewer

This project runs in two modes and has a lightweight JSON import layer for published match snapshots.

## 1. Local admin / analysis mode

Runs on the operator laptop. It is allowed to use CPU/GPU, OpenCV, YOLO and local video files.

Flow:

1. Open `/admin-panel` locally.
2. Create a match with metadata, teams and rosters.
3. Upload raw video.
4. Calibrate the real pitch geometry. Current pitch dimensions: `30.0 x 47.4 m`.
5. Run detection/tracking analysis.
6. Review artifacts and resolve identity: `raw tracker_id -> identity_candidate -> player_id`.
7. Generate a publishable match package.
8. Publish the package either to local JSON storage or to production, depending on `ORLIK_PUBLISH_TARGET`.
9. Use replace mode when deliberately overwriting a duplicate or corrected package.

The local app may store raw video, overlays, debug files, full tracks and temporary cache.

## 2. Production viewer mode

Runs on a small server. It should not run video analysis.

Allowed responsibilities:

- list published matches from JSON snapshots,
- show match reports,
- show player/team/season dashboards,
- serve imported heatmaps/assets in a later milestone,
- accept a secure import endpoint.

Forbidden responsibilities:

- upload raw video for analysis,
- run YOLO/OpenCV processing,
- generate overlay previews,
- store raw match videos.

## Environment modes

Recommended local laptop values:

```env
ORLIK_APP_MODE=local-analysis
ORLIK_PUBLISH_TARGET=remote-api
ORLIK_PRODUCTION_API_URL=https://your-production-domain.example
ORLIK_PRODUCTION_API_TOKEN=change-me
```

Recommended production VPS values:

```env
ORLIK_APP_MODE=production-viewer
ORLIK_PUBLISH_TARGET=local-json
ORLIK_ADMIN_IMPORT_TOKEN=change-me
```

`VITE_*` variables are public in the browser. Tokens must remain in backend env variables only.

## JSON storage

The MVP publish store is JSON because the project is still local-first and has only a small number of videos.

Default Docker path:

```text
/app/storage/published/matches/<published_match_id>/
```

Host path through Compose:

```text
backend/storage/published/matches/<published_match_id>/
```

Each published match directory contains:

```text
package.json
summary.json
```

`package.json` stores the full imported package as the source-of-truth snapshot. `summary.json` stores the lightweight list row used by `/api/published/matches`.

## Identity review before publish

Raw tracker IDs can flicker heavily, so the publish flow uses a candidate layer:

```text
raw tracker_id -> identity_candidate -> player_id
```

The backend exposes:

```text
GET /api/matches/{match_id}/identity-candidates
PUT /api/matches/{match_id}/identity-assignments
```

It stores local review artifacts:

```text
identity_candidates.json
identity_assignments.json
```

The candidate builder filters short/noisy raw tracklets and groups nearby tracklets by time and pitch position. The operator should assign candidates to roster players instead of assigning thousands of raw tracker IDs.

## Publishable package

`match_package.json` intentionally contains lightweight match data and metadata only. It does not contain raw video.

Current package contents:

- schema version,
- generated timestamp,
- match metadata,
- teams and players,
- pitch config if present,
- analysis report if present,
- reviewed tracklet/player assignments if present,
- identity candidates and identity assignments if present,
- references to generated artifacts.

Future milestones should expand it with:

- stints,
- player match stats,
- team match stats,
- event candidates,
- heatmap assets,
- import validation checksum.

## Current import API

Local publish generated from an existing match, using `ORLIK_PUBLISH_TARGET`:

```text
POST /api/matches/{match_id}/publish?replace=false
```

Forced local import into the current machine's JSON store:

```text
POST /api/matches/{match_id}/publish-local?replace=false
```

Generic package import, suitable for production/admin integrations:

```text
POST /api/admin/import-match?replace=false
Authorization: Bearer <ORLIK_ADMIN_IMPORT_TOKEN>
Content-Type: application/json
```

Management endpoints:

```text
GET    /api/published/matches
GET    /api/published/matches/{published_match_id}
DELETE /api/published/matches/{published_match_id}
```

Rebuild an existing publication from its original local match artifacts
(same operator action as „Zaktualizuj opublikowany raport”, exposed on the
published report page as „Przebuduj publikację”):

```text
POST /api/published/matches/{published_match_id}/rebuild
```

The server resolves the authoritative `source_match_id` from the stored
publication, rebuilds the package with the normal publication flow
(`build_match_package` + eligibility validation + atomic replace import),
and verifies before replacement that the rebuilt package still identifies
the same physical source. The stable `published-*` identity is preserved;
any mismatch fails closed without touching the stored publication. Logical
matches are never refreshed here — they observe the new snapshot through
the explicit #94 refresh flow.

Deletion is intentionally hard delete for now because this panel is meant for correcting duplicate imports and bad stats snapshots during MVP development. A later production version can add soft delete/audit logs.

## Merged (logical) matches are canonical published matches

Authoritative product invariant:

> A merged match is not a report about several matches.
> A merged match is one new match assembled from several physical match fragments.

Pipeline:

```text
physical published match A/B/C
        ↓
match-group manifest (INTERNAL provenance / aggregation definition)
        ↓
aggregation engine (ordered pins, digests, compatibility checks)
        ↓
canonical merged PublicMatchReport
        ↓
merged published-match projection:
    published/matches/published-merged-{uuid}/
        summary.json / public_report.json / provenance.json / heatmaps/
        (NO package.json — a merged match is not a physical package)
        ↓
/published/matches/{mergedPublishedId}/report
        ↓
the exact same PublishedMatchReportPage → PublicMatchReportContent
```

There is no separate user-facing aggregate report type. If
`PublicMatchReportContent` changes, merged matches get the change
automatically. The old `/published/match-groups/{groupId}/report` URL only
redirects to the canonical merged report.

Key properties:

- The merged published ID (`published-merged-{uuid}`) is allocated once per
  group, persisted in `match-groups/{groupId}/merged_projection.json`, and
  stays stable across regeneration, refresh, and video updates.
- Two ownership scopes protect this lifecycle: a short exclusive merged-ID
  reservation lock chooses that one stable ID, while the existing durable
  match-group maintenance ownership serializes the complete canonical
  projection lifecycle (stage, validate, promote) with refresh, update,
  regeneration, video maintenance, and deletion. A staged candidate is
  promoted only when its backing group still exists with the same manifest
  digest it was built from; ID reservation alone is not a projection lock.
- `source_kind` distinguishes `physical` from `merged` publications.
  `GET /api/published/matches/{id}` returns server-authoritative
  `capabilities`: physical matches offer `Przebuduj publikację`; merged
  matches offer `Regeneruj raport`, `Odśwież do najnowszych danych`, video
  generation, and external-video maintenance instead.
- `Regeneruj raport` rebuilds from currently pinned sources (no repinning).
  `Odśwież do najnowszych danych` repins changed sources atomically
  (all-or-nothing), preserves group and merged IDs, rebuilds the canonical
  report, reevaluates combined/external video freshness, and regenerates Key
  Moments — without auto-regenerating video. The same lifecycle previously
  known as #93/#94 is preserved.
- Aggregation semantics: durations/times/distances/counts are SUMMED, peak
  and max speeds use MAX, averages/percentages/rates (possession share,
  completion rate, workload, coverage) are RECOMPUTED from summed primitives,
  timelines are REBASED to logical time, heatmaps merge
  pitch-meter samples through the shared renderer, and average positions are
  recomputed from merged samples.
- Combined video and YouTube lifecycle keep their existing semantics but
  resolve server-side from the merged published match to its backing group;
  the frontend never infers group IDs.
- Existing `published/match-groups/` groups remain authoritative internal
  manifests; a lazy projection creates the stable merged published match on
  demand (create/regenerate/refresh/merged-match lookup) without destructive
  migration of source publications.

## Merged correctness guarantees (hardening)

### Canonical field semantics

- Momentum: fragment-local A/B signs are re-derived into canonical roles
  (canonical A ≥ 0, canonical B ≤ 0, `signed_score = a + b`); the dominant
  team follows the stable team, never the local label.
- Possession: `controlled_coverage = controlled / total` while
  `known_possession_coverage = (controlled + contested + free) / total`.
  Free and contested possession is known but not controlled; contested
  frames are never dropped. `processed_frames` is the preferred denominator,
  falling back to the summed categories.
- Player average speed uses summed `movement_time_sec` primitives (which
  include reviewed safe short-gap estimated movement), never detected time.
  Team movement has no time denominator in aggregate inputs, so team average
  speed stays explicitly merged-defined (distance over merged duration).
- Movement and core pass counts aggregate from `aggregate_inputs`
  primitives; `public_report.json` supplies presentation (names, colors)
  and extended classification without deeper primitives.
- A physical terminal display interval may end beyond the source's final
  decoded boundary only when its start is within the source interval. The
  canonical timeline clips that end to the source boundary. A primitive that
  starts at or outside the source boundary is invalid and fails closed; it is
  never preserved as a synthetic zero-width row.

### Spatial heatmaps: proven orientation or unavailable

Matching pitch dimensions do NOT prove matching coordinate orientation.
Merged heatmaps require, per fragment: valid spatial lineage
(`reviewed_player_heatmaps.source_snapshot_digest` equals the pinned
Reviewed Identity digest — mismatches fail closed) AND byte-identical pitch
calibration geometry (image points + dimensions, i.e. the identical
homography, hence identical pitch axes/origin). Otherwise the canonical
player `heatmap` is `None` (rendered as unavailable by the shared UI) —
never points drawn against fallback dimensions.

### Team Shape: evidence weighting and team-oriented space

Team Shape summaries and density grids average over valid frame-shape
samples, so fragments are weighted by `diagnostics.eligible_frames`
evidence — never video duration. Timelines rebase in seconds first (source
bin index × bin width + logical offset) and derive display minute/label
afterwards, keeping sub-minute boundaries exact. Team Shape lives in
team-attack-oriented space (already pooled across halves by the physical
computation), which is a different — and documented — contract from the raw
pitch-orientation rule used for heatmaps. Team_shape lineage entries are
validated against embedded package payloads where verifiable.

### Transactional canonical projection

The live projection is never mutated in place. Every rebuild stages a
complete candidate (report + heatmaps + summary + provenance + mirror
candidate), validates identity/digest coherence, then promotes it with
atomic directory replacement (with backup/restore). Any failure leaves the
previous complete projection untouched.

Refresh commits coherently: prepare the refreshed group candidate, prepare
the canonical projection candidate in staging, validate both, commit the
group pair transaction, then promote the projection. `provenance.json`
records the backing manifest digest; the read path (`GET
/api/published/matches/{id}`) fails closed on mismatch — rebuilding safely
when possible, returning an explicit conflict otherwise — so pins and the
user-facing report can never silently split.

### Static deployment mirror

The canonical operational report lives in
`backend/storage/published/matches/<published_id>/public_report.json`.
Its client deployment mirror is
`client/public/published/matches/<published_id>/public_report.json`, with any
referenced `heatmaps/` files beside it. Physical and merged publications use
the same staged, atomic promotion: a successful generation never promotes one
without the other, and their parsed JSON must be semantically identical.

Vite copies `client/public/` to the build output and Vercel deploys the files
that are committed in that Git revision. A local backend publication or report
regeneration materializes the mirror but cannot itself update an already
deployed Vercel bundle. Before deploying a new or regenerated public report,
commit only its public mirror (the JSON and referenced heatmaps), then verify
the Vite build contains those files. Never commit the backend publication,
source package, video, or other private storage artifacts.

### Deletion

Deleting a group resolves owned `published-merged-*` IDs BEFORE the group
directory (and its sidecar) disappears, then deletes the group under the
existing video/maintenance lock, then the canonical projection(s) and
static mirror(s) by explicit ID. Physical source publications are never
touched; concurrent video generation still blocks deletion; deletion is
idempotent over partially missing projections.

## Real merged-match acceptance

The canonical merged-publication path was accepted against a disposable copy
of three reviewed, physical publications. The copy keeps the operator's source
publications immutable while lifecycle actions exercise the same JSON store
contracts. Run the read-only reconciliation with:

```bash
PYTHONPATH=backend python backend/scripts/validate_merged_match.py \
  --storage-dir /path/to/disposable/published-store \
  published-merged-c33c30c0-b93d-46c7-9644-83bc4a8242f4
```

| Item | Accepted result |
| --- | --- |
| Group / canonical publication | `match-group-c3fbd48a-356d-44a0-a740-c630de69b527` / `published-merged-c33c30c0-b93d-46c7-9644-83bc4a8242f4` |
| Physical sources | `published-9c7485e4`, `published-6d8fc20c`, `published-5e62625e` (`9c7485e4`, `6d8fc20c`, `5e62625e`) |
| Exact durations / offsets | 1156.322 s at 0.000 s; 610.376 s at 1156.322 s; 356.323 s at 1766.698 s; merged 2123.021 s |
| Corgi movement | distance 20061.74 m, high intensity 3030.74 m, sprints 0, peak 22.55 km/h; independently reconciled as SUM/SUM/SUM/MAX |
| Paweł player row | one stable `player_id`; distance 1465.86 m, high intensity 253.41 m, 14 sprints, peak 21.02 km/h, average 6.05 km/h recomputed from movement time |
| Possession / passing | controlled coverage 0.2694, known coverage 0.6808; 217 completed from 471 attempts = 46.1% |
| Timeline / momentum | all possession and momentum intervals stay within 0–2123.021 s and canonical signs remain A >= 0, B <= 0 |
| Spatial / Team Shape | unavailable by design: canonical orientation and Team Shape evidence compatibility are not proven; merged heatmaps and average positions are fail-closed |
| Key Moments | two `ready` moments; their required bounds and production ordering tuple are independently validated |
| Hardened reconciliation | 258 checks: 255 passed, 0 failed, 3 Reviewed Identity digest recomputations explicitly unavailable because compact source inputs retain authoritative pins but not the physical Reviewed Identity artifacts |
| Lifecycle on copy | regenerate retained the same merged ID and pins; no-op refresh returned `current`; deleting the group removed only group/merged projections and preserved all 51 physical source files byte-for-byte |
| Longitudinal / source eligibility | longitudinal profiles read physical analysis matches, not merged publications; automated regressions preserve profile summaries and exclude `source_kind=merged` from merge sources |
| Compactness | aggregate inputs: 207,284 bytes total; source public reports: 3,109,916 bytes total; merged public report: 354,304 bytes |

### Combined-video closeout status (2026-09-06) — Refs #52

The real physical publications now contain completed, provenance-pinned
`reviewed_video.mp4` artifacts. A fresh disposable copy of those three
published packages and the match group was used for the production synchronous
generator; the authoritative store was not modified. The canonical projection
was regenerated in that copy before reconciliation, which again produced 255
passed checks, zero failures and the same three explicitly unavailable
Reviewed Identity recomputations.

The combined-video acceptance is nevertheless **not accepted**. The production
preflight returns `unavailable_source_video` with reason
`source_video_duration_mismatch` before it creates a generation. Its first
failing member is `published-6d8fc20c`.

| Published source | Logical duration used by the group | Published reviewed-video descriptor | Independent `ffprobe` duration | Absolute delta |
| --- | ---: | ---: | ---: | ---: |
| `published-9c7485e4` | 1156.322 s | 1156.288 s | 1156.290 s | 0.034 s |
| `published-6d8fc20c` | 610.376 s | 605.572 s | 605.572 s | 4.804 s |
| `published-5e62625e` | 356.323 s | 352.052 s | 352.054 s | 4.271 s |

The allowed production delta is 0.25 s. `ffprobe` agrees with the published
reviewed-video descriptors, so the discrepancy is not introduced by
publication copy. The two affected source MP4s also have shorter container
durations than their logical `frame_count / fps` metadata. The logical match
timeline therefore cannot be mapped safely onto the available video bytes.

Do not widen the tolerance, pad/trim the media, rewrite descriptor durations,
or publish a combined artifact for this group. Those changes would shift
fragment boundaries and make the canonical report, Key Moments and player
statistics describe different times than the video. A future remediation must
establish and prove one source-time mapping for the affected fragments, then
rebuild the affected projections from that mapping before attempting a new
combined-video acceptance. Until then #52 remains open and this result must be
referenced, not closed.

The canonical projection intentionally omits optional identity-coverage
presentation fields when the source contract cannot provide a reliable
denominator.

The reconciliation is read-only and does not call the merged-report builder.
It independently checks pinned public/aggregate semantic digests and identity
pins; source-derived team/player ID sets and duplicate rows; required numeric
fields; sparse per-team pass maps (absent team key means zero, but a missing
map or invalid present count fails); canonical player/team/pass metrics;
source-to-logical possession, momentum, and workload rebasing, including
absent versus explicitly empty workload windows; Key Moments bounds/order; and
declared fail-closed absence for unavailable spatial and Team Shape outputs. If a
future advanced capability is available, its mathematical correctness is
reported as `not_audited` until a separate independent oracle exists; artifact
presence alone is never an acceptance pass. The auditor uses final canonical
projection provenance for that classification, not the conservative compact
manifest preflight capability: package-level evidence may safely enable a
final output after preflight declares the compact inputs insufficient.
