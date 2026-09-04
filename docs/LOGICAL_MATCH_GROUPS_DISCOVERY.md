# Logical match groups — discovery and aggregation-input contract

> Current product model (canonical merged matches): the match group is an
> INTERNAL provenance / aggregation definition. Its output is a normal
> canonical published match (`published-merged-*`) rendered by the same
> `PublishedMatchReportPage`. There is no separate user-facing aggregate
> report type. See `docs/PUBLISHING_FLOW.md` ("Merged (logical) matches are
> canonical published matches").

This is the bounded Phase 0 design record for Issue #52.  It deliberately
does not add match groups, aggregate routes, aggregate reports, or an operator
interface.  A logical match remains a derived publication above independent
physical analyses.

## Findings

### Current publication pipeline

The current canonical flow for a reviewed, publishable match is:

```text
reviewed_identity_snapshot.json (semantic_digest)
  -> reviewed_player_stats.json / reviewed_player_heatmaps.json /
     reviewed_stats_readiness.json / reviewed_output_manifest.json
  -> build_match_package()
  -> ensure_package_publishable()
  -> import_match_package()
  -> write_public_match_report_bundle()
  -> storage/published/matches/published-<source_match_id>/public_report.json
```

The important implementation boundaries are:

| Role | Current code |
| --- | --- |
| Reviewed-output source validation | `backend/app/services/reviewed_match_report.py:reviewed_identity_package_status` |
| Finalized report projection | `backend/app/services/reviewed_match_report.py:build_reviewed_match_report` |
| Package construction | `backend/app/main.py:build_match_package` |
| Publish eligibility | `backend/app/main.py:ensure_package_publishable` |
| Persistent local publication store | `backend/app/services/json_publish_store.py:import_match_package` |
| Public report builder and heatmap rendering | `backend/app/services/public_match_report.py:build_public_match_report` and `write_public_match_report_bundle` |
| Local public report read path | `client/src/api.ts:getPublicMatchReport`, `client/src/components/PublishedMatchReportPage.tsx`, `client/src/components/PublicMatchReportContent.tsx` |

`import_match_package()` writes the full package, summary, public report and
public mirror beneath `backend/storage/published/matches/`.  It derives the
published ID deterministically as `published-<match.id>` and replaces the
whole directory on a replace-publish.  There is no current public report
semantic digest or aggregation-input artifact.

The reviewed publication gate is sound for a single source: the four reviewed
artifacts must agree on `source_snapshot_digest`, be completed/fresh, and the
reviewed output manifest must not be stale.  `build_public_match_report()`
then projects presentation data; it is not an aggregation input contract.

### Real artifact checked

`9c7485e4` currently has no published report or reviewed statistics bundle, so
it cannot demonstrate the published contract.  The audit used the real
published reviewed artifact
`backend/storage/published/matches/published-0c4412f2/` without mutating it.

Representative facts from that report/package:

- `public_report.json` is `schema_version: "0.1.0"`,
  `report_type: "public_match_report"`, and has one `source_match_id`.
- It carries durable `team_id` and `player_id` rows alongside local
  `team_label` (`A`/`B`).  The sample resolves Corgi as
  `team-corgi-1fc405` and Verisk as `team-verisk`.
- The package's reviewed artifacts agree on the Reviewed Identity semantic
  digest `f44070027e3a676da978041802a7897b2b5036110a6cefd7431657797d117611`.
- The player public projection carries distances, intensity, sprint count,
  peak/average speed and a rendered heatmap.  Its interactive heatmap has raw
  point `value` counts, but only canvas pixel coordinates and a 48 by 96 grid.
- The package has possession-frame and pass-count primitives, but the public
  report exposes a mixture of counts, percentages and A/B-labelled timelines.
- `reviewed_stats_readiness.json` explicitly says reviewed player attribution
  for passes and possession is `not_available`; therefore future aggregate
  report readiness must preserve that fact rather than upgrade it.

## Stable identifiers and provenance

| Concern | Current evidence | Phase 1 rule |
| --- | --- | --- |
| Source analysis | `match.id` / public `source_match_id` | Use only as the immutable physical-source key. |
| Published generation | deterministic `published-<match.id>` directory, overwritten on replace | Do not treat this ID alone as a generation pin. |
| Team | `match.teams[].id`, `team_config.teams[].team_id`, public `teams[].team_id` | Require a non-empty stable `team_id`; never use name or A/B. |
| Player | `match.teams[].players[].id`, resolved/public `players[].player_id` | Merge only equal non-empty real `player_id`; never names, shirt numbers, slots or tracklets. |
| Reviewed Identity | reviewed artifacts' shared `source_snapshot_digest` | Preserve it as source provenance, but it is not by itself a complete public-generation fingerprint. |
| Current public generation | no persisted semantic digest | Add a canonical report/input digest in Phase 1. |

The team data is sufficient to reconcile swapped local labels, provided every
selected source has stable `team_id` values.  A future compatibility validator
must build its team map by `team_id`; labels are source-local display/context
only.  Player IDs are suitable only when they are real roster IDs; anonymous
or missing player IDs must remain unmerged/unavailable.

`app.services.artifact_lineage.canonical_json_sha256()` already provides the
right canonical JSON hashing primitive: it removes volatile timestamp fields,
sorts keys and produces a `sha256:` digest.  Phase 1 should use it to persist
the source public-report semantic digest and an aggregation-input semantic
digest.  Future manifests pin the latter.  A group becomes stale when a member
is missing, has an unsupported input schema, or its current input digest no
longer equals the pinned one.

## Aggregation matrix

`YES` means the final contract can carry exact primitives.  `NO` means the
current public projection is insufficient and must remain unavailable; it must
not be approximated from presentation values.

| Metric | Current published fields | Policy | Enough now? | Required aggregation primitive / issue |
| --- | --- | --- | --- | --- |
| Player total distance | `players[].total_distance_m` | SUM | Yes | Exact reviewed movement total, keyed by `player_id`. |
| Team total distance | `teams[].total_distance_m` | SUM | Yes | Exact team total keyed by `team_id`; do not derive from anonymous/public player rows. |
| High-intensity distance | player/team scalar | SUM | Yes | Same scopes and stable keys. |
| Sprint count | player/team scalar | SUM | Yes | Same scopes and stable keys. |
| Peak speed | player/team peak scalar | MAX | Yes | Preserve metric definition/version and use MAX. |
| Player average speed | `avg_speed_kmh` only | RECOMPUTE | No | Movement-distance numerator plus the canonical `movement_time_sec` denominator. |
| Team average speed | `avg_speed_kmh` is absent/zero in the sampled team projection | RECOMPUTE | No | Explicit canonical team numerator/denominator, or unavailable. |
| Possession share | percentage plus A/B frame counts in timelines | RECOMPUTE | Partly | Per-`team_id` controlled frames, known/free/unknown frames and readiness; public A/B counts need mapping. |
| Pass attempts/completed/failed | global and team-label counters | SUM | Partly | Per-`team_id` counts and shared status semantics. |
| Pass completion rate | `completion_rate` percentage | RECOMPUTE | Yes after counts | `completed_passes / pass_attempts`, never average rates. |
| Identity/named coverage | current public report may omit it; reviewed stats expose global counts | RECOMPUTE | No | Exact confirmed/reliable denominator counts and declared coverage unit. |
| Player heatmap | rendered PNG; interactive binned point values | MERGE COUNTS | No | Metric-coordinate raw bin counts, pitch dimensions and canonical orientation/transform. |
| Attacking momentum | experimental timeline with A/B values | REBASE + CONCAT only | Partly | Time points, stable-team mapping, readiness/version and source offsets; no averaging across windows. |
| Team shape / average positions | already averaged summary/cells | RECOMPUTE | No | Per-cell/per-window raw sample weights and coordinate semantics. |
| Analyzed duration | `match.duration_sec` | SUM | Yes | Canonical analyzed duration and source timing metadata. |
| Logical timeline | source-local possession/momentum times | REBASE + CONCAT | Partly | Ordered member offsets supplied by future group manifest. |
| Quality/readiness | categorical report state | conservative worst / partial | Partly | Per-metric readiness, coverage and source status. |

Important caveats:

- The current reviewed player speed computation is `total_distance /
  movement_time_sec`, not an average of already rounded fragment speeds.
- `playing_time_sec` is intentionally unavailable in reviewed player stats;
  using `detected_time_sec` as a speed denominator would change meaning.
- Current public player heatmap points are raw bin values, but the report does
  not prove a canonical pitch orientation.  Same-looking grids are not enough
  to allow a cell-wise merge.
- Current team-shape output has only averages and average-grid cells.  It has
  no weights, so weighted aggregation is unsafe.

## Proposed `aggregate_inputs.json` v1

The public report cannot be used as the aggregation source.  Phase 1 should
write this compact, deterministic artifact atomically alongside a successful
published source at:

```text
storage/published/matches/published-<source_match_id>/aggregate_inputs.json
```

It must be built only from the publishable reviewed package and canonical
source reports.  It must contain no video, raw tracklets, Reviewed Identity
snapshot, tracker IDs or observation-level trajectories.

```json
{
  "schema_version": "1.0.0",
  "aggregation_policy_version": "1.0.0",
  "source": {
    "source_match_id": "physical-match-id",
    "published_id": "published-physical-match-id",
    "reviewed_identity_digest": "sha256-or-null",
    "public_report_semantic_digest": "sha256:...",
    "aggregation_input_semantic_digest": "sha256:..."
  },
  "timing": {
    "analyzed_duration_sec": 0,
    "fps": 0,
    "frame_count": 0
  },
  "teams": [
    {
      "team_id": "stable-team-id",
      "source_team_label": "A",
      "movement": {
        "total_distance_m": 0,
        "high_intensity_distance_m": 0,
        "sprint_count": 0,
        "peak_speed_kmh": 0,
        "average_speed": {"status": "not_available"}
      }
    }
  ],
  "ball": {
    "possession": {
      "status": "ready|not_available",
      "controlled_frames_by_team_id": {"stable-team-id": 0},
      "known_frames": 0,
      "free_frames": 0,
      "unknown_frames": 0
    },
    "passes": {
      "status": "ready|not_available",
      "attempts_by_team_id": {"stable-team-id": 0},
      "completed_by_team_id": {"stable-team-id": 0},
      "failed_by_team_id": {"stable-team-id": 0},
      "restart_attempts_by_team_id": {"stable-team-id": 0},
      "accepted_by_team_id": {"stable-team-id": 0},
      "attempts": 0,
      "completed": 0,
      "failed": 0,
      "restart_attempts": 0,
      "accepted": 0
    }
  },
  "players": [
    {
      "player_id": "stable-real-player-id",
      "team_id": "stable-team-id",
      "movement": {
        "total_distance_m": 0,
        "observed_distance_m": 0,
        "estimated_short_gap_distance_m": 0,
        "movement_time_sec": 0,
        "detected_time_sec": 0,
        "high_intensity_distance_m": 0,
        "sprint_count": 0,
        "peak_speed_kmh": 0
      },
      "identity": {
        "confirmed_observations": 0,
        "reliable_observations": null,
        "coverage_status": "ready|not_available"
      },
      "heatmap": {"status": "not_available"}
    }
  ],
  "identity_coverage": {
    "coverage_unit": "...",
    "confirmed_observations": 0,
    "reliable_observations": 0,
    "unresolved_observations": 0,
    "conflicted_observations": 0
  },
  "timelines": {
    "possession": {"status": "not_available", "windows": []},
    "attacking_momentum": {"status": "not_available", "points": []}
  },
  "spatial": {
    "pitch_dimensions_m": {"width_m": 0, "length_m": 0},
    "orientation": "unproven",
    "heatmaps": {"status": "not_available"},
    "team_shape": {"status": "not_available"}
  },
  "metric_readiness": {}
}
```

The exact v1 implementation should omit values rather than invent zeros when a
metric is unavailable.  It should include sparse raw **metric-coordinate**
heatmap bin counts only after Phase 1 proves a canonical orientation and grid.
Until then `heatmaps.status` stays `not_available`; it must not copy full
`positions_m` lists just to make grouping work.

### Phase 1 implementation boundary

Phase 1 implements this contract in
`backend/app/services/aggregate_inputs.py` and writes the server-only artifact
from `json_publish_store.import_match_package()` immediately after the exact
public report is generated.  It is not copied to `client/public`, returned by
the existing published-match read model, or written under `MATCHES_DIR`.

The implemented v1 keeps team movement in `teams`, global/per-team ball
primitives in `ball`, source-local possession/momentum primitives in
`timelines`, and all future aggregation decisions in `metric_readiness`.
`reviewed_player_stats.movement_time_sec` is now persisted as the exact
denominator used by Reviewed Identity's `avg_speed_mps`; older reviewed
artifacts that lack it expose player average speed as `not_available` rather
than receiving a fabricated denominator.

`aggregation_input_semantic_digest` is computed over the input document with
that field omitted.  `public_report_semantic_digest` is computed with the
existing canonical hash helper, so a republish caused only by `generated_at`
does not create a false stale generation.  A future group manifest pins both
the ordered input digest and source public-report digest; the input digest is
the authoritative aggregation member pin.

## Spatial compatibility

The reviewed heatmap source stores `positions_m` and pitch dimensions.  The
public report converts those to a 48 x 96 pixel canvas using
`pitch_meter_binned_canvas_heatmap_v1`; its positions are therefore display
coordinates, not a documented cross-source spatial contract.  Pitch dimensions
alone do not establish direction/orientation.  Team shape has 6 x 10 average
cells and summary averages but no sample weights.

Phase 1 must record an explicit canonical-orientation invariant from the
calibration/coordinate pipeline and test it.  Until that proof exists, group
creation may aggregate non-spatial metrics but must expose heatmap and
team-shape as unavailable.  It must not mirror, flip or sum spatial data.

## No-double-count boundary

Current longitudinal player/team profile builders enumerate only physical
directories under `MATCHES_DIR`:

- `backend/app/services/player_profiles.py:build_player_profile_stats`
- `backend/app/services/team_profiles.py:build_team_profile_stats`

This is the desired exclusion boundary today because published reports live
under `PUBLISHED_DIR`, outside `MATCHES_DIR`.  Future match-group storage must
remain a sibling such as `storage/published/match-groups/`, use an explicit
`report_type: public_aggregate_match_report`, and never create a synthetic
child directory under `MATCHES_DIR`.  Phase 2 needs a regression showing that
creating/regenerating/deleting a group changes neither profile appearance
counts nor longitudinal totals.

## Phase 2: manifest store and compatibility boundary

`backend/app/services/match_groups.py` stores one durable, UUID-backed
manifest at:

```text
storage/published/match-groups/match-group-<uuid>/manifest.json
```

The caller supplies only ordered `published_id` values and editable group
metadata.  The service reads the current source `aggregate_inputs.json` and
`public_report.json`, verifies both canonical digests and the supported
physical public-report schema/type, then derives and pins the source IDs,
aggregate-input and public-report contract versions, Reviewed Identity digest
and sequential logical timeline offsets itself.  It never reads `package.json`,
source video, raw tracking artifacts or `MATCHES_DIR`.

Core compatibility is fail-closed: every member must expose exactly the same
two stable `team_id` values; the local A/B labels may be swapped.  Player rows
may differ between fragments, but a repeated stable `player_id` must retain its
stable `team_id`.  Duplicate published or physical source members are rejected.

The stored group digest is canonical and self-excluding.  It changes with
meaningful metadata, source order, pins and logical offsets, but not technical
timestamps.  Later validation reads only `manifest.json`,
`aggregate_inputs.json`, and `public_report.json`.  A current source whose
verified pin has changed is reported as `stale`; it is never silently repinned.
Missing/tampered sources are precise invalid/stale reasons, while unsupported
input versions are incompatible.

Spatial orientation remains a metric capability, not a core membership gate.
Current groups can be `compatible` for non-spatial work while `spatial` and
`team_shape` remain unavailable.  Differing pitch dimensions produce a spatial
capability incompatibility only; this phase does not transform or merge them.

Creation stages the manifest outside the authoritative directory and swaps it
in only after all source checks pass.  Updates use an atomic JSON replacement,
so a failed source validation leaves the previous manifest bytes intact.
Deletion removes only the group directory.  No group store operation touches
published physical source files or the physical-match profile boundary.

## Refreshing to current physical publications

`POST /api/published/match-groups/{group_id}/refresh-to-latest` is the explicit
operator lifecycle action for a group whose stable `published_id` members were
republished in place.  The server rebuilds a candidate manifest from those
same ordered IDs, preserves the original `source_match_id` identity and group
metadata, validates compatibility, and builds the canonical aggregate report
before committing either document.

The paired manifest/report replacement is rollback-safe.  It never creates a
missing group directory, so a concurrent delete cannot be resurrected.  The
operation shares the existing durable combined-video ownership lock: refresh
is rejected while video generation is active, and neither refresh nor its
preview invokes ffmpeg.  A no-op leaves both existing bytes untouched.

Crash safety uses a small transaction-marker protocol, not one atomic
filesystem commit: before replacing either file, the previous coherent pair
is staged to `.manifest-report.*` recovery files plus a
`.manifest-report.transaction.json` marker.  Both replacements are then
`os.replace()` calls followed by a directory fsync; only afterwards is the
marker removed.  A restart that finds the marker rolls back to the previous
coherent pair before any authoritative read, so a crash between the two
replacements can never be served as a valid group.  A live maintenance owner
instead fails closed with `match_group_maintenance_in_progress` until the
commit finishes.

Readers never trust two independent files blindly.  The public report route
serves one coherent snapshot: manifest, report, manifest again, accepted only
when both manifest digests agree and the report's own semantic digest plus
its ordered source lineage (group, published, source-match and pin digests)
belong to that exact manifest generation.  A snapshot that moves under the
reader is retried a bounded number of times; anything else fails closed with
a structured 409 instead of a mixed NEW-manifest/OLD-report response.

All logical-group mutations share one maintenance ownership contract: normal
report regeneration, whole-group update (definition plus report under one
reservation), refresh, delete and video maintenance serialize on the same
durable per-group lock.  Only a genuine `video-generation-*` owner is ever
reported as `generating`; a live refresh/delete/report/update owner surfaces
as the structured `match_group_maintenance_in_progress` conflict instead.

`Regeneruj raport` rebuilds the aggregate report from the currently pinned
sources and never repins them; `Odśwież do najnowszych danych` is the only
operation that advances source pins, and only to the current authoritative
contents of the same ordered stable publications.  Refresh commits no
combined-video regeneration, no YouTube upload/rebinding, no Review rerun and
no analysis rerun.  A report-only source change keeps a ready combined video
(and a linked external video) current; a changed logical-video input digest
marks them stale through the existing provenance projections while the stored
video generations and `external_video.json` bytes stay untouched.

`GET /api/published/match-groups/{group_id}/refresh-preview` exposes only the
server-calculated `current`, `refreshable`, or `blocked` state.  The browser
does not choose member IDs or digests.  Refresh deliberately does not create a
new combined video or mutate external-video metadata; their existing stale
status projections determine any subsequent operator action.

## Mergeable implementation phases

1. **Aggregation input contract.** Implement the versioned builder above at
   publish time, canonical digests, strict stable-ID validation and focused
   tests.  No group APIs or UI.
2. **Group manifest and compatibility.** Add storage outside `MATCHES_DIR`,
   ordered source pins, timeline-offset validation and fail-closed team/player,
   schema, freshness and spatial checks.
3. **Aggregation engine.** Add an allowlisted metric registry (SUM, MAX,
   recompute, rebase-concatenate), a semantic aggregate digest and a compact
   aggregate public report.  Explicitly omit unavailable spatial/shape data.
4. **Lifecycle safety.** Add staleness on source republish/delete, atomic
   regeneration, source links and no-double-count regression coverage.
5. **API and operator/public UX.** Add CRUD/order/compatibility preview only
   after the server owns all aggregation math; then expose the public aggregate
   route.

This sequence keeps every physical source independently inspectable and avoids
changing Review, raw tracking, source publication semantics or Issue #48's
generic match payload work.
