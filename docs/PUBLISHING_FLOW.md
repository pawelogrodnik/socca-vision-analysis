# Local analysis admin vs production viewer

This project should run in two modes.

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
8. Send/import that package to production.

The local app may store raw video, overlays, debug files, full tracks and temporary cache.

## 2. Production viewer mode

Runs on a small server. It should not run video analysis.

Allowed responsibilities:

- list published matches,
- show match reports,
- show player/team/season dashboards,
- serve imported heatmaps/assets,
- accept a secure import endpoint in a future milestone.

Forbidden responsibilities:

- upload raw video for analysis,
- run YOLO/OpenCV processing,
- generate overlay previews,
- store raw match videos.

## Publishable package

The first implementation creates `match_package.json` locally. This package intentionally contains lightweight match data and metadata only. It does not contain raw video.

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

## Future production import

Recommended API shape:

```text
POST /api/admin/import-match
Authorization: Bearer <ADMIN_TOKEN>
Content-Type: multipart/form-data or application/json
```

The production backend should validate the package schema before inserting data into a database.
