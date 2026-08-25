# Required Review navigation investigation

## Current state machine

`IdentityExceptionReviewPanel` owns one loaded page in `cases`, its local cursor in `index`, the server-page position in `pageOffset`, and the `hasMore` value returned with that page. `getReviewedIdentityReviewProgress()` is the source of truth for a page and its authoritative filter counts; `resolveReviewPageNavigation()` only computes the next positional request from that snapshot.

The Required lifecycle (`knownRemaining` and `durableSavesInWindow`) changes only after a durable correction response. It is independent from read-only `Następny` / `Poprzedni` browsing. At forty durable saves it requests a hot replenish; true completion runs the canonical finalize.

## Two different operator actions

### Read-only navigation

No correction is saved, so the server queue has not changed. `cases`, `pageOffset`, `hasMore`, and the active filter all describe the same current server snapshot. A numeric offset produced by that snapshot is safe: page 0 can request 40, page 40 can request 80, and Previous can return to the prior offset.

### Review mutation

After a durable Required correction (including exact Mixed staging), the hot queue can shrink, reorder, or gain newly promoted work. The current local cases can still be shown under the hot-workstation contract, but their original positive server offsets no longer name the same logical page. The next server-page request must re-anchor at the current head (offset 0). A genuine structural mutation already takes the existing fail-closed authoritative reload path.

## Empty local page

`hasMore` is trustworthy only for the server snapshot that returned it. After a mutation, an empty local page with an old `hasMore=false` is not proof that the active filter is empty. The panel must fetch hot progress at offset 0 for the active filter once. That response establishes a new navigation snapshot: it either supplies cases, provides coherent zero filter counts plus a nonzero global count, or enters the existing global-completion/recovery path. Accepting that fresh snapshot prevents an empty-verification loop.

## State ownership

- Server snapshot: `pageOffset`, `hasMore`, `reviewFilters`, `totalRemaining`, and the initial Required `knownRemaining`.
- Local working state: `cases`, `index`, and the post-save decrements to Required lifecycle/count display.
- Positional-offset validity: a small frontend navigation state. It is clean after an accepted progress response, invalidated only by a durable Required mutation, and reset on match, queue, or team-filter changes.
