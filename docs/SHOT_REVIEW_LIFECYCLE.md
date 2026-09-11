# Future Shot Review lifecycle

Issue [#133](https://github.com/pawelogrodnik/socca-vision-analysis/issues/133)
freezes the manual Corgi–Verisk benchmark before a production shot detector is
implemented. This document is the product contract for the later detector work
in [#66](https://github.com/pawelogrodnik/socca-vision-analysis/issues/66).
It deliberately does not implement a detector, review UI, shot map, public
statistics or xG.

## Candidate status is not football truth

A detector row is a suggestion, never an automatically confirmed shot. The
initial lifecycle has four operator-visible origins/statuses:

```text
suggested -> accepted
suggested -> rejected
manual
```

Only accepted suggestions and manually added, operator-confirmed shots become
canonical football events. Raw suggestions, including very high-confidence
ones, must not enter team/player shot totals, shot maps, public reports, Match
Story, Key Moments or downstream analytics.

Canonical events retain provenance as either `accepted_suggestion` or
`manual_operator`. Candidate confidence and detector reasons remain candidate
provenance; they do not become the authority for a confirmed event.

## Recall-oriented candidate generation

The first detector should rank plausible candidates, but optimize for reducing
manual video watching rather than minimizing the suggestion count. A
reviewable false-positive queue is preferable when it substantially improves
recovery of real shots. No numeric candidate quota is a product policy.

The first #66 benchmark must report:

- recall against all 31 manually confirmed shots and the identities of misses;
- total candidates and candidates per 10 minutes;
- hard-negative hits and false-positive categories;
- timing error relative to the approximate manual contact anchors;
- outcome breakdown (`goal`, `on_target`, `blocked`, `off_target`);
- team breakdown; and
- player identity only as diagnostic metadata.

The 14 curated hard negatives are valuable regression cases, but are not a
representative negative population. They must not be used to claim global
precision. Once a detector has produced a candidate list for this match, that
entire list can be manually classified for a meaningful first precision-like
measure.

## Operator workflow

The later UI should follow the Suggested Key Moments pattern:

```text
video/player + suggested shot queue + accept/reject + manual add
```

For a suggestion, the operator confirms or corrects team and outcome. The
Polish outcome choices are `Celny`, `Niecelny`, `Zablokowany` and `Gol`, with
machine values `on_target`, `off_target`, `blocked` and `goal`. `Gol` remains
distinct from `on_target`; analytically, shots on target are `on_target +
goal`.

Player is optional. A clear action can be canonical with, for example,
`team = Verisk`, `player = null`, `outcome = blocked`. A manual add has the
same required team/outcome, optional player, and should prefill the current
video time when possible.

## Durable review decisions

Rejected suggestions must survive ordinary report refresh/rebuild so the same
candidate is not returned as unreviewed. Accepted and manual shots are
operator-owned canonical annotations and must survive candidate-policy or
detector-version changes. Suggestions may be regenerated; operator decisions
must remain a separate durable layer.

Confirmed shots may later become eligible inputs to Key Moments, but raw
suggestions never are. Pass detection follows the same sequence only after the
shot layer is validated: manual pass goldset, detector candidates, operator
review, then canonical events.

## Evaluation-only benchmark boundary

`backend/tests/fixtures/shot_goldset_v1.json` is an evaluation fixture, not
production data. Production runtime code must never import, load, query or use
its timestamps/labels to generate shot candidates. Only tests, benchmark
scripts and audit/evaluation tooling may consume it.
