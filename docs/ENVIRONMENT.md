# Environment configuration

Local analysis and production viewer deployments use the same codebase, but different environment values.

## Local laptop

Use a local `.env` file in the repository root. This file is ignored by Git.

```env
ORLIK_APP_MODE=local-analysis
ORLIK_PUBLISH_TARGET=remote-api
ORLIK_PRODUCTION_API_URL=https://your-production-domain.example
ORLIK_PRODUCTION_API_TOKEN=change-me
ORLIK_ADMIN_IMPORT_TOKEN=
ORLIK_DEFAULT_PITCH_WIDTH_M=30
ORLIK_DEFAULT_PITCH_LENGTH_M=47.4
VITE_API_BASE_URL=http://localhost:8000
VITE_APP_MODE=local-analysis
```

In this mode the laptop may upload raw video, run YOLO/OpenCV, create debug artifacts and publish an approved `match_package.json` to production.

## Production VPS

Production should receive values from GitHub Secrets or a deployment-time `.env` file on the server.

```env
ORLIK_APP_MODE=production-viewer
ORLIK_PUBLISH_TARGET=local-json
ORLIK_ADMIN_IMPORT_TOKEN=change-me
ORLIK_DEFAULT_PITCH_WIDTH_M=30
ORLIK_DEFAULT_PITCH_LENGTH_M=47.4
VITE_API_BASE_URL=https://your-production-domain.example
VITE_APP_MODE=production-viewer
```

In this mode the server should not upload raw video or run analysis. It accepts already-reviewed match packages through:

```text
POST /api/admin/import-match?replace=false
Authorization: Bearer <ORLIK_ADMIN_IMPORT_TOKEN>
```

## Public vs secret variables

`VITE_*` variables are bundled into the browser and are public. Never put tokens in `VITE_*`.

Secrets belong only in backend variables:

```text
ORLIK_PRODUCTION_API_TOKEN
ORLIK_ADMIN_IMPORT_TOKEN
```

## Publish target

`ORLIK_PUBLISH_TARGET=local-json` imports into local JSON snapshots under:

```text
backend/storage/published/matches/<published_match_id>/
```

`ORLIK_PUBLISH_TARGET=local-db` is accepted as a legacy alias, but it also uses the JSON store.

`ORLIK_PUBLISH_TARGET=remote-api` sends the local match package to:

```text
<ORLIK_PRODUCTION_API_URL>/api/admin/import-match
```

The request uses:

```text
Authorization: Bearer <ORLIK_PRODUCTION_API_TOKEN>
```
