# Post-YOLO Reprocess Debug Flow

Use this flow when YOLO inference has already been run for a clip or match and
you only want to test backend logic changes:

- team recognition
- tracklet splitting and merging
- stable player IDs
- RAW? resolver/debug overlay
- ball post-filtering and interpolation
- player/team/possession stats

The normal client analysis flow is unchanged. Client-triggered analysis still
runs the full pipeline: YOLO plus post-processing.

## Reprocess An Existing Match

```bash
PYTHONPATH=backend backend/.venv-mps/bin/python backend/scripts/reprocess_analysis.py \
  --match-id <match_id> \
  --label debug-after-change
```

By default this creates a new directory under:

```text
backend/storage/reprocess/
```

The source match directory is not modified unless you explicitly pass it as
`--output-dir`.

## Reprocess A Benchmark Or Artifact Directory

```bash
PYTHONPATH=backend backend/.venv-mps/bin/python backend/scripts/reprocess_analysis.py \
  --source-dir backend/storage/benchmarks/<run_dir> \
  --video /absolute/path/to/video.mp4 \
  --label resolver-debug
```

If the source directory contains `benchmark_input.json` with `video_path`, the
`--video` argument can usually be omitted.

## Ball Artifacts

If `ball_candidates.json` exists, reprocess rebuilds `ball_tracks.json` from
the candidates before applying the current post-filter logic. This lets us test
ball tracking/interpolation changes without rerunning ball YOLO.

If only `ball_tracks.json` exists, reprocess uses that stored file as input.

## Optional Debug Outputs

```bash
PYTHONPATH=backend backend/.venv-mps/bin/python backend/scripts/reprocess_analysis.py \
  --match-id <match_id> \
  --debug-overlay \
  --raw-overlay
```

`--debug-overlay` writes `debug_identity_overlay.mp4`.
`--raw-overlay` rebuilds `overlay_preview.mp4` from stored `tracks.json`.
