# Local analysis admin vs production viewer

This project runs in two modes and now has a lightweight SQLite import layer for published match snapshots.

## 1. Local admin / analysis mode

Runs on the operator laptop. It is allowed to use CPU/GPU, OpenCV, YOLO and local video files.

Flow:

1. Open `/admin-panel` locally.
2. Create a match with metadata, teams and rosters.
3. Upload raw video.
4. Calibrate the pitch.
5. Run detection/tracking analysis.
6. Review artifacts and, later, resolve `tracklet -> player_id -> stint`.
7. Generate a publishable match package.
8. Click `Publish/import to DB` to insert the approved snapshot into SQLite.
9. Use `Replace in DB` when deliberately overwriting a duplicate or corrected package.

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

## Publishable package

`match_package.json` intentionally contains lightweight match data and metadata only. It does not contain raw video.

Current package contents:

- schema version,
- generated timestamp,
- match metadata,
- teams and players,
- pitch config if present,
- analysis report if present,
- references to generated artifacts.

Future milestones should expand it with:

- reviewed player assignments,
- stints,
- player match stats,
- team match stats,
- event candidates,
- heatmap assets,
- import validation checksum.

## Current import API

Local import generated from an existing match:

```text
POST /api/matches/{match_id}/publish-local?replace=false
```

Generic package import, suitable for production/admin integrations:

```text
POST /api/admin/import-match?replace=false
Content-Type: application/json
```

Management endpoints:

```text
GET    /api/published/matches
GET    /api/published/matches/{published_match_id}
DELETE /api/published/matches/{published_match_id}
```

Deletion is intentionally hard delete for now because this panel is meant for correcting duplicate imports and bad stats snapshots during MVP development. A later production version can add soft delete/audit logs.
