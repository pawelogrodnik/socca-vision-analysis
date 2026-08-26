# Required and Mixed interleaving — implemented lifecycle

Reviewed Identity exposes two peer mandatory operator queues:

```text
Review
├── Required
└── Mixed
```

Both queues can contain current blocking work at the same time. Switching
between them is navigation only: it does not persist a decision, decrement a
count, reproject Review or finalize identity. The backend remains the final
authority for completion, readiness and publication, so an empty client queue
never bypasses the finalization gate.

## Queue and focused-read responsibilities

The full blocking-only Mixed queue is read only at lifecycle boundaries:

- initial entry into a manual Mixed session;
- after a structural Mixed save, because topology and Required ordering may
  have changed;
- once during bounded drift reconciliation when a previously known focused
  case is authoritatively reported as missing, resolved or nonmandatory.

The dedicated focused endpoint is used for:

- a Required `resolve-now` handoff to one exact durable Mixed `case_id`;
- previous/next buttons and keyboard arrows in a manual Mixed session;
- the next case after persisting `unresolved_complex_mix`.

Each focused read validates exact ownership and scope, materializes one case
and returns that case's evidence. The client keeps the current card visible
until the requested evidence is ready. Normal M1→M2 navigation therefore costs
one focused read and no full queue rebuild.

If a manual focused read reports `missing`, `no_longer_unresolved` or
`not_in_mandatory_queue`, the client performs exactly one authoritative full
queue reconciliation, preserves navigation direction, selects the logical
current successor and validates that successor with another exact focused
read. It never displays a stale local sibling without that validation and
never loops reconciliation. Transport errors, malformed responses and a
second membership change remain explicit recovery errors.

Resolve-now intentionally does not reconcile to a different case. If its
exact staged source is stale or absent, it fails closed and returns an explicit
recovery state rather than silently opening unrelated Mixed work.

## Exact durable source identity

`case_id` is generated from the exact staged Review source and is the durable
Mixed identity. Its source records `scope_kind`, `candidate_subject_id`,
`review_target_id`, optional `continuity_group_id`,
`source_ownership_digest` and the exact ownership range. A raw subject ID is
not sufficient because one subject can own multiple Review intervals.

Focused reads and saves use this exact source. If the source is completed, the
backend reports that fact; the client does not guess a replacement source or
requeue historical ownership.

## Non-structural decisions

An ordinary deferred Required decision validates the hot state version, exact
source ownership, actionable unit and action scope. The durable decision is
then projected into the current hot queue. Staging `mixed_players` creates an
exact unresolved durable marker and removes only its exact Required source.

Saving `unresolved_complex_mix` is also a durable operator decision: it records
that the evidence does not support a safe temporal split. The application must
not force a player guess. Navigation to the next Mixed card still passes
through the exact focused endpoint; drift on that next card uses the same
single reconciliation described above and never retries the save.

## Structural Mixed split lifecycle

A temporal split changes canonical Review topology. Its implemented path is:

```text
save exact Mixed case
→ validate case_id, digest, ownership and scope
→ derive authoritative child segments
→ build canonical segment topology exactly once
→ persist all segment decisions atomically as one batch
→ project the persisted decisions without rebuilding topology
→ mark reviewed recompute required
→ invalidate the old hot generation
→ explicitly reproject Review exactly once
→ build authoritative progress exactly once
→ write that generation back as warm hot state
→ route from authoritative Required/Mixed counts
```

The explicit reproject uses `operator_evidence=False`: it does not eagerly
render all crops. It uses `leave_hot_state_warm=True`, so an immediate Required
offset-0 progress read consumes the just-built warm generation and performs no
second canonical progress build. The old model in which the next GET was
expected to perform cold recovery is not part of the implemented lifecycle.

Returning to Required preserves the active queue and team filter but resets
pagination to offset 0. This retains the PR #40 invariant that offsets and
`has_more` belong only to the generation that produced them.

## Evidence and performance contract

Mixed evidence is lazy. Initial manual entry materializes the first relevant
case; exact focused navigation materializes only the requested case. Normal
M1→M2 movement never calls `build_mixed_review_queue()`. Structural saves are
the deliberate exception: their single full authoritative reload is required
because the queue topology may genuinely change.

The split path builds one canonical segment topology, persists a batch, and
projects those decisions. It does not build topology a second time for the
same save and it does not trigger navigation-time finalization.

## Safety and scope invariants

- Required and blocking Mixed remain peer mandatory queues.
- Mixed remains blocking-only under the PR #42 scope policy. A certain
  TEAM_STATS_ONLY player-only marker can stay durable without entering the
  mandatory queue.
- Stale or unclassifiable Mixed ownership remains visible as a fail-closed
  blocker and cannot be assigned from incomplete historical evidence.
- A non-actionable coverage safety blocker remains intentional and blocks
  finalization/publication even when it has no ordinary operator card.
- Only structural mutations reproject the full Review lifecycle. Navigation
  and focused evidence reads never finalize or rerun video analysis.
