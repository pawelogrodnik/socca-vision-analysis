# Coverage-driven Reviewed Identity

## Forensic baseline

This note records the evidence used before changing Reviewed Identity completion
semantics. It is intentionally based on frozen tracking and identity artifacts;
no detector or tracker was rerun.

- Base SHA: `b108073b8e4244065883f031b9b7236d7e8863a8`.
- PR #24 is merged at that SHA and Mixed Players remains a separate workflow.
- Failing published report: `published-5ededbe1`.
- Source match workspace: `backend/storage/matches/5ededbe1`.
- Video duration: 1156.322 s.
- Initial Audit selected 8 diverse frames (22 unique visible subjects, no
  near-duplicate frame pair) and saved 58 decisions, including 53 named roster
  decisions.
- Safe seed propagation accepted 53 candidate subjects, rejected 5
  propagations and left 1610 of 1663 candidate subjects unresolved.
- Canonical effective-observation coverage from the reviewed snapshot:
  - global: 56,576 / 479,291 = **11.80% named**;
  - Team A: 12,011 / 244,391 = **4.91% named**;
  - Team B: 44,565 / 197,055 = **22.62% named**;
  - unknown-team observations: 0 / 37,845 named.
- The previous progress read model reported 1256 `pending_optional` units
  containing 381,014 observations, or **79.49%** of all on-pitch detected
  observations. 1251 of them (380,451 observations) were classified as
  `long_unresolved_safe_anonymous`.
- It reported zero important decisions and an empty `next_cases` queue.
- Workflow state therefore had `normal_blocking = 0`, `mixed_blocking = 0` and
  reached `ready_to_finalize`, while `reviewed_stats_readiness.json` still had
  `status = completed`.
- Movement corroborates the coverage failure without being used as the primary
  denominator:
  - Team A: 679.21 m named / 10,878.32 m team = **6.24%**;
  - Team B: 2,723.73 m named / 11,050.94 m team = **24.65%**;
  - old Team A report: 10,570.19 m named / 11,419.87 m team = **92.56%**.

### Root-cause classification

| Candidate cause | Classification | Evidence |
| --- | --- | --- |
| Initial Audit frame cap (8/10) | CONTRIBUTING | It limits bootstrap opportunities, but the selected frames were diverse and produced 58 decisions. |
| Initial Audit reducer case cap (12) | CONTRIBUTING | It bounds completion evidence for the bootstrap stage; it must not mean match completeness. |
| Initial Audit sampling quality | NOT SUPPORTED | The real selection beat its random baseline, covered 22 subjects and contained no near duplicates. |
| Long-match candidate fragmentation | PRIMARY upstream workload cause | 1663 candidate subjects exist for a 19-minute half. |
| Safe subject-scoped propagation | CONTRIBUTING, correct safety behavior | Only 53 exact subjects were safely seeded; loosening this would invent identities. |
| Seed conflicts/rejections | NOT SUPPORTED as primary | Five rejected propagations and zero created conflicts cannot explain the missing 422k named observations. |
| Large anonymous subjects become optional | PRIMARY | 1256 optional units own 381,014 observations. |
| `next_cases` contains only semantic high-priority units | PRIMARY | None of the coverage debt entered the operator queue. |
| Workflow ignores optional named-coverage debt | PRIMARY | `important_decisions_remaining == 0` unlocked finalization. |
| No canonical named-coverage readiness gate | PRIMARY | Stats readiness was `completed` at 11.8% named coverage. |
| Freshness-only report readiness | PRIMARY | Matching digests were sufficient for reviewed/public packaging. |
| New shadow-hardened detector | NOT SUPPORTED | The failing frozen run predates that detector. |

Exact control points are
`identity_reviewed_progress._unit`,
`identity_reviewed_progress.build_reviewed_identity_progress`,
`review_workflow_state._issue_evidence`,
`identity_reviewed_stats.build_reviewed_stats`, and
`reviewed_match_report.reviewed_identity_package_status`.

## Canonical coverage model

The primary denominator is the existing unique detected on-pitch
`(tracklet_id, frame)` observation after the canonical merge order:

`tracklet assignment -> canonical ownership -> exact audit override -> segment correction -> safety demotion`.

An observation contributes to named coverage only when its effective identity
is `confirmed` and it has a canonical roster `player_id`. Team-known anonymous
observations remain valid for team statistics but never count as named.

Coverage is exposed globally and per effective team. Distance coverage remains
diagnostic only.

## Experimental completion policy v1

The local calibration set contains one easy 90-second match, two difficult
six-minute matches and the failing 19-minute half. The policy is therefore
explicitly versioned and experimental rather than presented as universal.

1. Semantic conflicts remain first in the normal queue.
2. Mixed subjects remain exclusively in Mixed Players.
3. Remaining unreviewed units are ranked by their unique currently unnamed
   on-pitch observations.
4. Per effective team, the largest units remain coverage blockers until the
   residual *unreviewed* observation debt is at most 10% of that team's
   reliable observations. This 10% residual budget is supported by the old
   report's 92.56% Team A named-distance ratio and keeps the known 90-second
   Team A fixture at zero extra cases (9.2% residual), while still exposing the
   real long-match failure.
5. The residual budget is not a case cap. Every meaningful case remains known
   to backend completion semantics; UI pages are presentation only.
6. Explicit team-only/anonymous decisions acknowledge a review unit but do not
   increase named coverage. The report must continue to identify named stats as
   partial.
7. An explicitly complete roster may additionally require 90% named coverage.
   With an unspecified or partial roster, clearing significant review debt
   yields `ready_with_review`, not a false claim of complete named coverage.

Calibration predicts approximately 27 additional cases for the 90-second
fixture, about 269 for difficult six-minute fixtures and about 578 for the
failing 19-minute match. This matches the product expectation that useful work
may exceed 100 decisions and that a workload above roughly 500 is a strong
fragmentation warning.

The frozen `5ededbe1` validation after applying the exact implementation
produces **531** actionable coverage cases (zero semantic conflicts), keeps the
workflow in `exceptions`, and blocks publication. The paginated public response
contains 20 cases, is about 27 KB, and is served from the current progress cache
in roughly 54 ms locally. Desktop validation confirmed forward and backward
navigation across the 20/21 boundary without recomputing identity.

Workload levels are diagnostic only:

- `normal`: fewer than 150 significant cases;
- `elevated`: 150-499;
- `excessive`: 500-999;
- `critical`: at least 1000.

They never truncate the queue or relax readiness.

## Migration and readiness

- Initial Audit remains an 8/10-frame high-leverage bootstrap.
- Progress and coverage policy versions are bumped so old cached progress is
  rebuilt when a source match is reopened.
- Existing published reports remain immutable and readable without coverage
  fields.
- Newly generated reviewed readiness and public reports carry canonical
  coverage status and metrics.
- `incomplete` blocks finalization/public packaging; `ready_with_review`
  explicitly communicates partial named-player scope.
- Corrections remain deferred: one click writes semantic state and marks the
  downstream snapshot/stats/render dirty, without YOLO, tracking or rendering.
