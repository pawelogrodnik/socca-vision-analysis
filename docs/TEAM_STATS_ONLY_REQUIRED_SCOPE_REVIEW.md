# TEAM_STATS_ONLY Required scope — pre-implementation review

## Dependency graph

```text
match.json.identity_review_scope
  -> team_review_scope(match_doc, team_label)
  -> materialized reviewed-identity units
  -> apply_coverage_policy(units, coverage, pair_index, match_doc)
       -> semantic / material continuity / coverage selections
       -> coverage_policy.next_cases
  -> project_reviewed_identity_progress(...)
       -> summary.important_decisions_remaining
  -> review_workflow_state._issue_evidence(...)
       -> issues.normal_blocking
       -> Mixed Players gate
```

`match.json.identity_review_scope` is the source of truth. `team_review_scope()` first reads its `teams.A/B` values, then only uses the legacy per-team setting when no explicit value exists. `identity_review_scope_digest()` is already captured in cold progress and the hot-state freshness payload; a scope edit therefore invalidates the projection without rerunning video analysis.

Team attribution is derived per unit from effective observations in `_coverage_impact()`. It writes `coverage_team_label`, while the materialized unit retains `source_team_label` and `effective_team_label`. A certain Team B identity ambiguity has all of those labels safely at B and is only about the concrete player. An A/B attribution uncertainty has Team U, a cross-team/conflict marker, or another `_has_team_uncertainty()` signal; it affects team statistics and cannot be suppressed as opponent player-name work.

## Current policy path and defect

`apply_coverage_policy()` begins by collecting every actionable
`pending_high_priority` unit into `semantic`. The main loop then derives
coverage attribution, handles `_has_team_uncertainty()`, classifies material
continuity, and only after that skips `TEAM_STATS_ONLY` coverage candidates.

Consequently, a certain Team B high-priority player identity conflict can
already be in `semantic`, and a certain Team B material-continuity case can
already be in `material_continuity`, before the Team-B scope exclusion runs.
Both are appended to `next_cases`, and then to the normal Required count. This
is the policy defect observed by PR #41 diagnostics.

Named coverage has a later scope guard and a `TEAM_STATS_ONLY` named target of
`None`; it must remain nonblocking. The fix must centralize eligibility after
the unit has enough attribution evidence, then apply it consistently to
semantic, continuity, and coverage selections.

## Mixed Players path

`build_mixed_review_queue()` loads unresolved markers and exact source
observations, then currently reports `mixed_case_summary()` as every unresolved
marker. `project_reviewed_identity_progress()` stores that queue, and
`review_workflow_state._issue_evidence()` maps its unresolved count directly to
`mixed_blocking`. Thus a certain Team B-only mixed player source currently
blocks the next stage even though it only needs player separation, not team
attribution.

The new policy must classify each exact source independently: certain
COMPLETE_ROSTER identity mix is blocking; certain TEAM_STATS_ONLY identity-only
mix is nonblocking/diagnostic; Team U or cross-team attribution mix remains
blocking. Exact owned observations and source digests remain authoritative, so
a Team-B source cannot suppress a sibling Corgi source that shares a candidate
subject.

## Derived artifacts and cache behavior

Cold progress is built by `build_reviewed_identity_progress()` and the
deferred-save hot projection uses `project_reviewed_identity_progress()`.
`reviewed_identity_hot_state.json` checks its schema and freshness inputs,
including the scope digest and mixed-marker fingerprint. Durable progress checks
its schema, coverage policy version, and scope digest. Because Required and
Mixed eligibility semantics change, the coverage policy version and hot-state
schema must be bumped so old projections rebuild once from frozen detections,
existing decisions, and exact marker sources. No canonical identity assignment
or decision needs rewriting.

## Existing test coverage and gaps

Existing coverage tests already verify Team-B named coverage does not create a
90% target, and PR #41 tests expose unexpected queue cases. Existing workflow
and mixed tests verify unresolved markers lock the Mixed stage. Missing are
scope-aware tests for high-priority certain Team B identity conflicts, Team B
material continuity, exact Team-B-only mixed sources, per-source sibling
behavior, and hot/cold policy-version rebuild agreement. The implementation
adds those cases together with the real-shaped 6 Corgi + 2 safety Team B + 41
out-of-scope Team B queue regression.

## UX dependency

`IdentityExceptionReviewPanel` currently renders
`ReviewedIdentityCoverageDebtSummary` inline after compact coverage and before
the evidence workstation. The detailed summary is read-only, client-local
state; it can be moved into an accessible dialog without touching queue state,
correction context, or API calls. The compact coverage header remains visible
and only an on-demand details control belongs in the normal workstation flow.

## Performance evidence

The local 519 MB review fixture `23391dfb` was measured with three cold and
five warm isolated read-only runs. Compared with current `main` (PR #41), this
policy revision measured 1,160.5 ms versus 1,206.6 ms median cold progress and
16.5 ms versus 17.0 ms median warm progress. The public page fell from 116,807
to 116,130 bytes and temporary hot state from 1,823,323 to 1,785,584 bytes.
The modal is local React state and makes no Review API call.
