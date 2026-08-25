# Required and Mixed interleaving — dependency review

This document records the pre-implementation dependency review for making
Reviewed Identity's Required and mandatory Mixed work parallel operator queues.
It was prepared from `main` after PR #42 was merged.

## Current graph

```text
IdentityReviewWorkspace
  -> identityReviewStage(workflow.phase)
  -> IdentityExceptionReviewPanel       (phase: exceptions)
     -> GET review-progress (Required / Optional MAX)
     -> POST corrections (including staged `mixed_players`)
  -> MixedPlayersReviewPanel             (phase: mixed_players)
     -> GET mixed-players
     -> POST mixed-players/resolve
  -> finalizeReviewWorkflow              (only final readiness)

review-progress
  -> reviewed_identity_hot_state
  -> project_reviewed_identity_progress
  -> coverage policy + build_mixed_review_queue
  -> workflow issues / ReviewWorkflow

mixed_players correction action
  -> action gate resolves one exact review unit
  -> reviewed_identity_mixed_players durable marker
  -> deferred hot projection removes its Required source

saveMixedPlayerResolution
  -> exact durable marker and source digest
  -> split child targets / segment decisions
  -> recompute_required + hot-state invalidation
  -> authoritative review reprojection
```

## State and mutation classification

### Non-structural mutations

An ordinary deferred correction is non-structural when it changes only one
already materialized review unit. The correction action gate validates the hot
state version, exact source ownership digest, current actionable unit and
action scope. `update_hot_state_after_deferred_save()` then re-projects the
queue from the compact hot inputs. The client may remove the saved local card,
but re-anchors at offset 0 at its existing lifecycle boundaries.

Staging `mixed_players` from Required is a durable decision, but it does not
split track topology. It creates an exact unresolved marker whose `source`
contains `scope_kind`, `candidate_subject_id`, `review_target_id`, optional
`continuity_group_id`, and `source_ownership_digest`. The source is removed
from Required by the hot projection. It is nevertheless a queue mutation, so
positive offsets from the previous Required snapshot are no longer valid.

### Structural Mixed mutations

`saveMixedPlayerResolution()` is structural for a temporal split. It verifies
the exact `case_id` and `source_subject_digest`, resolves the durable source,
creates exact child segment targets and segment decisions, marks review
recompute required, and invalidates the hot state. It is not a normal hot save.
The next progress read must build an authoritative projection; it can change
topology, current named coverage, Required coverage selection and ordering.

The `unresolved_complex_mix` operation also updates the durable marker and
marks recompute required, although it does not create child assignments.

## Authority and lifecycle

- Hot state is invalidated after a Mixed resolution in
  `post_match_reviewed_identity_mixed_resolution`. It must not be trusted
  until a following `GET review-progress` materializes it again.
- A cold/recovery read is required after any structural Mixed resolution and
  after a recoverable stale-save conflict. It must start at offset 0 while
  preserving the active Required team filter where applicable.
- A pure Required/Mixed tab switch is navigation only: it must not call a
  correction, create a marker, decrement a count, or finalize.
- The durable truth survives a browser refresh: the staged marker lives in
  `reviewed_identity_mixed_players.json`, and Required source filtering reads
  it again when progress is projected.

## Exact source identity

The unique staged Mixed key is `case_id`, generated from the exact review
source (`source_case_id`). Its durable source payload additionally carries
`review_target_id` and `source_ownership_digest`. `candidate_subject_id` is
not enough: one subject can own several exact source intervals. Any
resolve-now handoff must target by `case_id` and source digest, never by
subject alone.

## Required pagination and PR #40

`IdentityExceptionReviewPanel` uses a 40-case window. Its
`RequiredReviewNavigationState.queueMutatedSinceSnapshot` is set after a
durable Required mutation. `resolveRequiredReviewPageRequest()` then converts
any later positive-offset page request into an offset-0 re-anchor. This is the
PR #40 invariant: `offset` and `has_more` only describe the generation that
produced them. A Mixed structural save must trigger the same reset before
returning to Required; retaining offset 40/80 could skip or resurrect work.

## Scope and coverage policy

`build_mixed_review_queue()` uses `mixed_review_relevant_for_scope()`. PR #42
therefore exposes only blocking Mixed cases: COMPLETE_ROSTER, cross-team,
Team-U/attribution-uncertain and stale/unclassifiable sources. A certain
TEAM_STATS_ONLY Verisk player-only Mixed marker remains durable but does not
appear in the mandatory queue or count.

Required units are selected by the current coverage policy after semantic and
material-continuity blockers. Coverage candidates represent current named
coverage debt, not a client-side counter. A structural Mixed save is followed
by the canonical progress rebuild, so coverage policy runs over the real child
decisions. It may remove old coverage-only Required cards once the target is
met; it must retain semantic and safety Required blockers.

## Existing sequential assumptions to remove

1. `derive_review_workflow_state()` marks `mixed_players` locked whenever
   `normal_blocking > 0`, and `identityReviewStage()` renders one panel from
   that phase.
2. `post_match_reviewed_identity_mixed_resolution()` currently requires the
   `review_mixed_players` workflow action, which is unavailable while Required
   work remains.
3. `MixedPlayersReviewPanel.advanceAfterSave()` calls canonical finalization
   when its local Mixed queue becomes empty. Empty Mixed is not Review
   completion when Required work or another readiness gate remains.
4. The coverage dialog still describes Mixed as locked by Required work.

## Implementation boundary

The change should make workflow state report parallel review availability while
preserving the strict final gate. The client chooses `required` or `mixed` as
the visible subqueue; backend scope, exact ownership, coverage and readiness
remain authoritative. A resolve-now handoff may be ephemeral UI state, but
must refer to a durable exact `case_id`, and its correctness cannot depend on
that ephemeral state surviving refresh.
