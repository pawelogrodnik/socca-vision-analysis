# AGENTS.md — Backend

These instructions apply to `backend/`.

## Stack

- Python + FastAPI.
- OpenCV for video I/O and geometry helpers.
- Ultralytics YOLO for the current tracking adapter.
- Keep the backend usable on CPU, even if slower. GPU support is optional and environment-dependent.

## Backend architecture

Keep the backend split into layers:

```text
app/
  main.py           FastAPI app and routes only
  models.py         Pydantic request/response contracts
  config.py         paths and environment config
  services/
    video.py        video metadata, frames, encoding helpers
    analysis.py     orchestration and current adapters
    geometry.py     pitch polygon, homography, footpoint math
    tracking.py     future tracker/tracklet helpers
    stats.py        future distance, speed, sprint, heatmap stats
```

Avoid placing computer-vision logic directly in FastAPI route handlers.

## API and data contracts

- Validate input with Pydantic models.
- Keep response shapes stable and documented in `docs/DATA_MODEL.md` when changed.
- Use clear field names: `match_id`, `track_id`, `tracklet_id`, `player_id`, `frame_index`, `time_sec`.
- Do not return huge per-frame arrays directly from API endpoints unless needed. Store large artifacts on disk and return artifact links/metadata.
- Use explicit error messages for missing video, missing pitch config and failed model loading.

## Video analysis rules

- Process videos frame-by-frame or in small batches. Do not load whole videos into memory.
- Keep `frame_stride`, `max_seconds`, `imgsz`, `conf` and `device` configurable.
- Write intermediate/final artifacts deterministically:
  - `overlay_preview.mp4`
  - `tracks.json`
  - `analysis_report.json`
  - `heatmap_all_tracks.png`
- When adding long full-match processing, move it to a background job/queue instead of blocking the request.

## Computer vision rules

- Filter detections by footpoint inside pitch polygon before treating them as players.
- Treat raw tracker IDs as temporary. Do not use them as permanent player identity.
- Keep adapter-specific code behind a common interface/shape.
- Report confidence/coverage when possible.
- Preserve both image coordinates and pitch coordinates once homography is available.
- Keep ball tracking separate from player tracking; ball detection will need different assumptions.

## Geometry/statistics rules

- Use pitch dimensions from `pitch_config.json`; do not hardcode one global pitch size inside stats.
- Distances and speeds should be computed from pitch-meter coordinates, not raw pixels.
- Smooth trajectories before calculating distance/speed to avoid jitter-inflated stats.
- Track missing data explicitly instead of pretending interpolation is ground truth.
- Use absolute time in seconds for calculations; format as `MM:SS` only in presentation layers.

## File/storage rules

- Keep user data under `storage/` and out of git.
- Never trust upload filenames for paths. Store uploaded video as `video.<ext>` inside generated match directory.
- Avoid deleting original uploads during analysis.
- Keep generated artifacts replaceable: rerunning analysis may overwrite previous analysis outputs for the same match.

## Python style

- Use type hints for public functions and service boundaries.
- Prefer small pure functions for geometry/stat calculations.
- Avoid broad `except Exception` unless re-raising with useful context.
- Use `pathlib.Path` for filesystem paths.
- Avoid global mutable state for match-specific data.
- Keep model loading explicit; if caching models later, isolate it in a dedicated module.

## Performance expectations

- Optimize for correctness and debuggability before speed.
- Short clips must work first; full-match processing comes later.
- Avoid unnecessary video re-encoding. Only generate overlay videos when requested/needed.
- For CPU mode, use smaller YOLO models and reasonable `imgsz` defaults.

## Future backend milestones

When implementing these, keep them separate:

1. `tracklet_resolver`: merge/split raw track segments.
2. `identity_assignments`: map tracklets to real players.
3. `stints`: calculate on-pitch intervals with substitutions.
4. `position_stats`: distance, speed, sprints, heatmaps.
5. `ball_layer`: ball detection, smoothing and interpolation.
6. `event_layer`: possession, passes, shots and clips.

Do not mix these milestones into one giant analysis function.
