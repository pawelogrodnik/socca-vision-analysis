# Contact / Action Goldset v1 — materialization audit

## Authority and scope

The versioned fixture is [contact_action_goldset_v1.json](../../backend/tests/fixtures/contact_action_goldset_v1.json). It is evaluation-only ground truth for six windows on the merged Corgi–Verisk timeline; it does not evaluate, tune or modify any production contact, pass, possession, ball or shot pipeline.

Source precedence used during materialization:

1. the six raw operator-note files `W1`–`W6`;
2. existing canonical Shot Review snapshot for shot identity, time and outcome;
3. this consolidated [FULL_PASS_GOLDSET.md](FULL_PASS_GOLDSET.md) as a normalization and cross-check source.

No detector candidate was promoted to truth. `possible_missing_manual_events` is intentionally empty: this PR does not run a detector-versus-goldset evaluation.

## Materialized result

| Item | Count |
| --- | ---: |
| Windows | 6 |
| Manual contact/action events | 113 |
| PASS | 50 |
| RESTART | 20 |
| INTERVENTION | 18 |
| CONTROL | 7 |
| CONTEST | 6 |
| GK_COLLECTION | 6 |
| OTHER | 6 |
| Game-state intervals | 6 |
| Identity notes | 12 |
| Canonical Shot Review references | 11 |
| Explicit ambiguous events | 5 |
| Unresolved operator questions | 0 |
| Possible missing manual events | 0 |

All normal manual events preserve `approx_time_sec`, have `aligned_time_sec: null`, and therefore do not pretend that an approximate operator anchor is frame truth. The eleven shot references retain their canonical timing. This includes W6 at `32:09.5`, replacing the approximate `32:11` manual anchor only for the Shot Review reference.

## Cross-check results

- The raw prose and consolidated document agree on the football order of W1–W6.
- Game-state intervals are separate from events: replacement-ball delay, goalkeeper holds, foul/free-kick preparation and W5's free-kick setup are not represented as fake repeated contacts.
- Overlay identity flickers are stored as `identity_notes`, never as player substitutions or team changes.
- The attempted missed touch by Mateusz at W1 `00:46` is retained as `OTHER + ATTEMPTED_CONTACT` with `contact_confirmed: false`.
- The W2 Paweł centrostrzał after the saved shot is retained as uncertain surrounding action, not promoted to a shot reference or a shot-goldset label.
- W5's start carries a `source_video_boundary` technical note. It is not a football event.
- Events deliberately retained only to close a sequence beyond a selected 75-second range use `context_only: true`; they are not in-window labels for later scoring.

## Canonical shot linkage

All canonical shots that fall in the six selected windows are referenced by stable Shot Review ID: two in W1, two in W2, none in W3, two in W4, four in W5 and one in W6.

One known authority conflict is deliberately preserved rather than silently resolved in this PR:

- The raw W4 operator note describes the `22:48.5` Verisk shot as off target, while current canonical Shot Review records `blocked`. The goldset reference keeps the canonical `blocked` value, because this task must not mutate Shot Review. The raw disagreement is retained in that reference's `manual_note` for the separately authorized canonical-state correction.

## No unresolved football questions

The operator supplied decisions for the initially ambiguous review points, including the W1 headed block, W2 centrostrzał exclusion, W4 outcomes and W6 canonical timing. Remaining uncertainty is represented explicitly where the operator's notes use words such as “probably” or leave an actor uncertain; it is not an unanswered review request.

## Reproducibility boundary

The markdown files remain the human trace. The JSON fixture is the machine-readable evaluation contract. Future evaluation must use the JSON plus the existing canonical Shot Review snapshot, not current detector output as a substitute for missing labels.
