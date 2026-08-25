# Reviewed Identity coverage debt — dependency review

## Canonical facts and existing projections

`reviewed_identity_snapshot.json` is the canonical source for effective identity. `load_effective_coverage_context()` joins it with `tracklets.json`, filters product/on-pitch observations and deduplicates `(tracklet_id, frame)`. The reliable denominator is every unique pair whose status is `confirmed`, `unresolved`, `conflicted`, `blocked` or `team_unknown`; named coverage is the subset with `confirmed` plus a canonical player id. `identity_coverage` is this derived read model, not a separate counter.

`materialize_reviewed_identity_units()` derives exact review ownership from frozen candidates, segments, material continuity and durable decisions. Pair ownership is explicit while cold data is built, then compact per-tracklet frame runs in the hot cache.

`apply_coverage_policy()` projects effective coverage and those units into Required cases, `residual_by_team`, readiness and Optional/MAX. It deduplicates potential named gains before selecting Required work and ranks MAX by marginal unique gain. `optional_audit` remains the authority for Team-A-only MAX figures: remaining actionable gain, safe maximum and unavailable residual.

Saved roster decisions are durable review decisions until canonical finalize updates the snapshot. Existing deferred roster semantics can show their probable effect in hot Review; non-naming decisions are deliberately excluded. Mixed markers live in `reviewed_identity_mixed_players.json`. A modern staged marker has a source digest and exact `owned_observations`, and suppresses only that source. Legacy whole-subject markers have no source descriptor, so hot projection must not narrow or attribute them optimistically.

## Projection path and hot-state contract

The cold builder gathers snapshot coverage, compact pair index, materialized units and Mixed queue, then calls `project_reviewed_identity_progress()`. `update_hot_state_after_deferred_save()` calls the same function after every durable save. It is therefore the one authority for cold and warm projections.

The hot state persists compact pair-index and ownership runs. A normal review-progress GET pages this read model; it does not reopen tracking artifacts, finalize corrections or rebuild canonical identity. HTTP pagination removes exact queue ownership from public cards but does not recalculate coverage.

## Coverage-debt accounting policy

`coverage_debt` starts with current reliable unnamed pairs and assigns each pair to only one bucket. The precedence is: already named (outside debt), saved roster naming pending canonical synchronization, current Required ownership, exact staged Mixed ownership, authoritative Optional/MAX marginal ownership, then unavailable residual. This keeps saved names out of ordinary work, preserves the Required workflow, moves only exact staged sources to Mixed, and reuses MAX authority.

Mixed means observations currently located in unresolved Mixed sources, never guaranteed future names. Cross-team, Team-U, legacy or malformed exact Mixed sources are never assigned to Team A or B; they are separate ambiguous/unattributed diagnostics. For every team, named plus unnamed equals reliable observations; the bucket union plus `unaccounted_unnamed_observations` equals unnamed. No number is clamped to hide a mismatch. All percentages use the current reliable team denominator, which a later team correction can change.

## Scope and freshness refinement

For `TEAM_STATS_ONLY`, unnamed player observations are explicitly represented as `not_required_by_scope`, not as unavailable identity debt. The only Required debt shown for that scope is a semantic team-attribution safety case; player-name coverage, Optional/MAX, saved roster naming and ordinary unavailable residual do not become operator work merely because the observations lack names.

Required debt has an authoritative queue-derived breakdown: semantic/team conflict, material continuity and named coverage. The client only renders these backend categories. Deferred correction responses now include the just-projected compact `coverage_debt` from the validated hot state, so saved Required decisions and exact Mixed staging update the explanation immediately without a GET, canonical finalize or queue rebuild.

`ambiguous.raw_marker_observations` is intentionally named as a raw marker total. It is not presented as a unique reliable-observation accounting value because legacy or overlapping markers cannot prove that stronger fact.

## Performance evidence

Read-only benchmark on local match `23391dfb` (519 MB fixture, three cold and five warm runs) compared commit `accfcd7` with this update. Cold progress median was 938.6 ms before and 941.5 ms after. Warm review-progress median was 16.4 ms before and 16.5 ms after. The public page grew from 115,034 to 115,904 bytes and the hot-state file from 1,821,682 to 1,822,490 bytes.

The sub-millisecond warm delta is within run variation and there is no material hot-read regression. The projection now maintains a shrinking `remaining_by_team` interval set while claiming buckets, rather than recomputing the full unnamed-minus-assigned difference for every unit.
