# Analysis Progress Reporting Plan

## Problem

Long match analysis can appear stuck when a coarse progress percentage stays on
one value for a long-running internal step, especially during stable overlay
rendering or possession overlay rewriting. Users need to know whether the
process is alive, which milestone is active, and which milestones remain.

## Goals

- Show a stable milestone plan for every background analysis job.
- Persist heartbeat timestamps so the UI can distinguish slow work from a stale
  worker.
- Report chunk-level progress separately from post-processing milestones.
- Keep generated artifacts visible and linkable as soon as they exist.
- Mark interrupted jobs as terminal instead of leaving them in `running`.

## Milestones

1. Queued
2. Starting analysis
3. Camera motion model
4. YOLO chunk analysis
5. Merge player tracks
6. Merge ball observations
7. Build ball tracks
8. Player identity and stats
9. Render stable overlay
10. Possession and pass candidates
11. Render possession overlay
12. Final reports
13. Completed

## Backend Contract

Each analysis job JSON should include `progress_plan`:

- `active_step_id`
- `last_heartbeat_at`
- `last_artifact_at`
- `active_step_elapsed_sec`
- optional `current` counter, for example `2/10 chunks`
- `steps[]` with `pending`, `running`, `completed`, or `failed`

The job should preserve the last real progress value when interrupted. It
should not fake `100%` unless the job completed.

## UI Behavior

- Display the active step label, current counter, elapsed step time, and
  heartbeat age.
- Display all planned steps as a compact checklist.
- Warn if a running job has no heartbeat for more than 90 seconds.
- Treat `completed`, `failed`, `interrupted`, and `cancelled` as terminal
  states.

## Follow-ups

- Stream frame-level progress from overlay rendering.
- Add artifact-level status, for example `stable_overlay_preview.mp4 writing`.
- Add cleanup tooling for leftover `.raw.avi` files after interrupted renders.
- Add a lightweight server-side endpoint for active worker diagnostics.
