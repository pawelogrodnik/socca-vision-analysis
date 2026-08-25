# Reviewed Identity throughput investigation

## Sources of truth and derived state

Operator decisions are durable in the roster/slot, segment, material-continuity and exact mixed-player decision stores. The mixed store keeps a lossless source tuple and owned observations for staged cases. The canonical Reviewed Identity snapshot, progress document, workflow state and coverage/readiness are derived from those stores and frozen analysis artifacts. `reviewed_identity_hot_state.json` is a compact, restart-safe derived projection: it contains the materialized exact review units, projection inputs and a monotonic revision; it is never the authority for a decision.

## Current transitions

A normal deferred correction is authorized against a versioned hot unit, persisted immediately, marks `reviewed_identity_recompute_required.json`, then reprojects the hot state and advances its revision. It does not rebuild the canonical snapshot. Exact `mixed_players` staging persists the exact marker and recompute marker too, but is currently reported as `review_topology_changed`; the API invalidates hot state, the UI reloads progress, sees `recompute_required`, and auto-finalizes. That turns a queue-routing action into the observed blocking `progress -> finalize -> progress` cycle.

`recompute_required` means the canonical snapshot has not incorporated one or more durable decisions. It does not mean the versioned hot queue is unsafe. A temporal split, split retirement/supersede, manual-slot creation, stale ownership conflict or hot-state write failure genuinely invalidates exact queue topology and must fail closed to an authoritative rebuild.

## Queue and pagination finding

The Required queue is an ordered, shrinking projection. After a local page of 20 cases is saved, the frontend currently requests `offset = pageOffset + 20`. Since the server has already removed those 20 sources, that offset skips the first 20 *remaining* sources. With 242 cases, resolving the first 20 leaves 222; `offset=20` therefore starts at original case 41, skipping original cases 21--40. This is a real skip bug, not merely a display-count issue.

## Intended synchronization contract

Normal corrections and exact mixed staging remain durable per click and patch the hot projection. Exact mixed routing removes just its source from Required, records it once in Mixed Players, and preserves unrelated sibling sources. A dirty canonical snapshot can coexist with a safe hot working window. The client replenishes Required Review from offset zero with stable-key dedupe rather than advancing a mutable offset; an authoritative finalize is reserved for a working-window boundary (40 decisions), Required completion, a true structural mutation, hot-state recovery/conflict, or a workflow transition that requires canonical readiness.
