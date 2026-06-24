# Local analysis admin vs production viewer

This project runs in two modes and has a lightweight SQLite import layer for published match snapshots.

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
8. Publish the package either to local SQLite or to production, depending on `ORLIK_PUBLISH_TARGET`.
9. Use replace mode when deliberately overwriting a duplicate or corrected package.

The local app may store raw video, overlays, debug files, full tracks and temporary cache.

## 2. Production viewer mode

Runs on a small server. It should not run video analysis.

Allowed responsibilities:

- list published matches from SQLite,
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
ORLIK_PUBLISH_TARGET=local-db
ORLIK_ADMIN_IMPORT_TOKEN=change-me
```

`VITE_*` variables are public in the browser. Tokens must remain in backend env variables only.

## SQLite storage

The MVP database is SQLite because it is small, zero-admin and works well on a low-memory box.

Default Docker path:

```text
/app/storage/database/orlik.sqlite3
```

Host path through Compose:

```text
backend/storage/database/orlik.sqlite3
```

The database contains normalized summary tables:

```text
published_matches
published_teams
published_players
```

`published_matches.package_json` still stores the full imported package as a source-of-truth snapshot. The normalized tables are for listing, deletion and future season/player queries.

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

Forced local import into the current machine's SQLite:

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

Deletion is intentionally hard delete for now because this panel is meant for correcting duplicate imports and bad stats snapshots during MVP development. A later production version can add soft delete/audit logs.
