# Operator Review Workflow — Phase 1 contract

## Authority and scope

`GET /api/matches/{match_id}/review-workflow` is the sole authority for the
operator workflow and for entering the local report/publish path. It derives
state from persisted analysis and reviewed-identity artifacts; it never writes
files, rebuilds identity, computes statistics, or queues a render.

This contract does not alter detection, tracking, ReID, stitching, global
identity, stable-slot allocation, frame ownership, or uniqueness safety.

## Derived workflow

The fixed review stages are `initial_audit`, `exceptions`, `finalize`, and
`video_qa`. Each is `locked`, `current`, `processing`, `completed`, or `error`.
The workflow-level state is `unavailable`, `action_required`, `processing`,
`ready`, `complete`, or `error`; its current phase is one of
`initial_audit`, `exceptions`, `ready_to_finalize`, `rendering_review_video`,
`video_qa`, or `complete`.

Authoritative inputs are:

| Concern | Source |
| --- | --- |
| Analysis availability | `analysis_report.json` / match metadata |
| Bounded initial audit | selected audit observations plus fresh seeded-reduction completion evidence |
| Effective identity issues | `reviewed_identity_progress.json` matching the fresh reviewed snapshot digest |
| Identity freshness | `reviewed_identity_snapshot.json` source digest |
| Stats/current output | reviewed stats files, `reviewed_video_job.json`, and `reviewed_output_manifest.json` |
| Human QA | `reviewed_video_qa_approval.json` |

Technical diagnostics (for example a multi-slot tracklet) are not operator
blockers by themselves. The issue provider uses effective reviewed progress;
only unresolved/structurally unsafe effective identity is a blocker. A
frame-owned multi-slot tracklet with safe effective observations is diagnostic
only. A real ownership gap remains actionable when progress still reports it.

Initial audit completion is evidence based at observation level, never at frame
level. The seeded/reduced review infrastructure exposes a bounded set of
representative, high-value observation cases (at most 12; one representative
per relevant selected candidate subject). It is complete only when every such
case has an explicit stored disposition, including `skip`, or when the current
seeded reducer reports a deterministic safe stop because no further candidate
review remains. One click in each selected frame is therefore not completion
by itself, and every visible bbox is not mandatory. Missing or stale completion
evidence fails closed and keeps the audit open.

Cached reviewed progress is usable only when its
`source_snapshot_digest` exactly equals the current reviewed identity semantic
digest. Missing or stale progress blocks progression with
`review_progress_missing` or `review_progress_stale` and exposes only the
lightweight recompute action. The workflow GET never repairs this cache.

## Transition and orchestration table

| Operator event | Required work | Next state |
| --- | --- | --- |
| Initial-audit decision | Persist decision, rebuild seeded downstream review where available, finalize reviewed identity, rebuild effective progress, invalidate stale output | Initial audit or exceptions, based on fresh evidence |
| Exception/slot decision | Persist decision, finalize reviewed identity, rebuild effective progress, invalidate stale output | Exceptions or ready to finalize |
| Finalize for Video QA | Finalize identity, rebuild effective progress, build reviewed stats, queue/reuse reviewed render | `rendering_review_video` |
| Video-QA or already-approved correction | Persist correction, invalidate the approval by fingerprint lineage, finalize identity and progress; if no blockers build stats and queue/reuse one render | Exceptions if blocked, otherwise rendering and fresh QA required |
| Approve Video QA | Persist exact current identity/stats/output fingerprints | Complete |
| Retry render | Queue/reuse render only when identity and stats are current | Rendering |
| Retry recompute | Repeat the lightweight identity/progress refresh | Derived state |

`POST /reviewed-identity/finalize` remains a legacy lightweight review refresh:
it finalizes identity and caches progress but never builds stats or queues a
render. `POST /review-workflow/finalize` is the sole expensive boundary for
identity → progress → stats → one reviewed-video render. The legacy reviewed
video panel uses that workflow endpoint once for preparation while preserving
its minimap, ball, and roster-number options.

Lightweight review refresh never runs YOLO, tracking, global identity, or full
analysis; it never renders video or rebuilds reviewed stats. Rendering is only
queued by workflow finalization, Video-QA correction, or explicit retry.

## Gating and failures

Every workflow action passes one common transition check. A rejected transition
returns HTTP 409 with `detail.code`, the attempted action, a current workflow
snapshot, and a stable reason such as `analysis_not_completed`,
`initial_audit_incomplete`, `identity_issues_remaining`, `workflow_busy`,
`render_failed`, or `video_qa_not_ready`.

Finalize requires completed analysis and initial audit, zero blocking issues,
and no active incompatible render. QA approval requires a completed output and
current identity, stats, output, and approval lineage. Retry is idempotent:
the existing current job is reused; an active incompatible job is rejected.
An old render never unlocks QA because its snapshot digest must equal the
current identity fingerprint.

QA approval is the only workflow fact persisted here. Its artifact is versioned
and contains the exact reviewed identity digest, stats digest, output digest,
and output manifest fingerprint. Workflow phases are always derived. Any later
identity change makes an old approval stale by fingerprint mismatch without a
revoke mutation. A complete workflow still allows `review_video` and
`correct_video_identity`; after a correction report/package/publish locks again
until the replacement output is explicitly approved.

If a lightweight refresh fails after a decision was saved, the API returns a
structured `review_recompute_failed` workflow error. `retry-recompute` reruns
the required lightweight orchestration. Render failure keeps identity valid but
sets the workflow to error and exposes only `retry_render` as the next action.

## Report/package/publish compatibility

New or replacement local package/publish operations require
`review_complete == true`; otherwise the backend rejects them with
`review_not_completed` (HTTP 409). Existing published reports remain readable.
An already published local match may remain visible, but a replacement publish
after local identity changes must satisfy current QA approval.

## API and polling

Workflow state returns schema `1.0.0`, phase/status, per-step state and reason
codes, issue counts, currentness, processing, blockers, required action, and
allowed actions. UI may fetch on entry and after a mutation. During rendering it
may poll this lightweight endpoint no more often than about every 30 seconds,
with no overlapping requests. The endpoint reads small JSON metadata only; it
does not call renderers, CV models, frame/crop generators, snapshot finalizers,
or stats builders.

## Compatibility and Phase 2 UI

Existing identity and report endpoints remain available. Their product-facing
mutations call the shared review refresh and return additive workflow state.
`buildReviewReadiness` remains for legacy presentation but is not authoritative
for Step 4. The Phase 2 UI will expand only the current stage, collapse complete
stages, show future locks with reasons, offer one primary action, show automatic
processing, move reviewed video into Step 3, and place stable slots, candidate
subjects, tracklets, raw IDs, source diagnostics, and technical coverage behind
Developer details. Legacy manual snapshot/recompute controls, duplicate
readiness, mandatory stable overlay review, and manual package generation are
not part of the normal operator route.
