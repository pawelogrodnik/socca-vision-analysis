# Pass Team Attribution v2

## Current attribution path

- Contact candidates inherit team fields from controlled possession segments built from the production player event timeline.
- Event candidates copy contact `team_label`, `team_id` and `team_name` unchanged.
- Pass Policy v1 derives source/target relationship, completion outcome and `count_for_team_label` from those event teams.
- V2 is evaluation-only here: canonical global-identity slot team is preferred; event team is a fallback; no stable-ID prefix heuristic is used.

## Error audit

- Matched actor/receiver attribution errors audited: **16**.
- `W1-E002` @ 11.0s · gold Verisk→Verisk · v1 Corgi→Verisk · source `event-0003` / target `event-0004` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `None` · possession frames source `{'A': 3}` / target `{'B': 7}`
- `W1-E003` @ 15.0s · gold Verisk→Verisk · v1 Corgi→Verisk · source `event-0009` / target `event-0010` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `None` · possession frames source `{'A': 9}` / target `{'B': 12}`
- `W1-E006` @ 25.0s · gold Verisk→Verisk · v1 Corgi→Corgi · source `event-0019` / target `event-0020` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `UPSTREAM_IDENTITY_ERROR` · possession frames source `{'A': 5}` / target `{'A': 6}`
- `W1-E010` @ 31.0s · gold Verisk→Verisk · v1 Verisk→Corgi · source `event-0024` / target `event-0025` · actor `None` · receiver `UPSTREAM_IDENTITY_ERROR` · possession frames source `{'B': 57}` / target `{'A': 13}`
- `W2-E006` @ 565.0s · gold Corgi→Corgi · v1 Verisk→Verisk · source `event-0327` / target `event-0328` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `UPSTREAM_IDENTITY_ERROR` · possession frames source `{'B': 1}` / target `{'B': 9}`
- `W3-E005` @ 1278.0s · gold Verisk→Verisk · v1 Corgi→Corgi · source `event-0055` / target `event-0056` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `UPSTREAM_IDENTITY_ERROR` · possession frames source `{'A': 94}` / target `{'A': 4}`
- `W3-E009` @ 1293.0s · gold Verisk→Verisk · v1 Corgi→Corgi · source `event-0058` / target `event-0059` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `UPSTREAM_IDENTITY_ERROR` · possession frames source `{'A': 4}` / target `{'A': 16}`
- `W3-E016` @ 1327.3s · gold Verisk→Verisk · v1 Corgi→Corgi · source `event-0076` / target `event-0077` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `UPSTREAM_IDENTITY_ERROR` · possession frames source `{'A': 1}` / target `{'A': 9}`
- `W4-E003` @ 1352.5s · gold Verisk→Verisk · v1 Verisk→Corgi · source `event-0101` / target `event-0102` · actor `None` · receiver `UPSTREAM_IDENTITY_ERROR` · possession frames source `{'B': 3}` / target `{'A': 1}`
- `W4-E004` @ 1353.3s · gold Verisk→Verisk · v1 Corgi→Corgi · source `event-0102` / target `event-0103` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `UPSTREAM_IDENTITY_ERROR` · possession frames source `{'A': 1}` / target `{'A': 3}`
- `W4-E005` @ 1355.0s · gold Verisk→Verisk · v1 Corgi→Verisk · source `event-0107` / target `event-0108` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `None` · possession frames source `{'A': 4}` / target `{'B': 4}`
- `W4-E006` @ 1356.0s · gold Corgi→Corgi · v1 Verisk→Corgi · source `event-0108` / target `event-0109` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `None` · possession frames source `{'B': 4}` / target `{'A': 15}`
- `W4-E010` @ 1361.0s · gold Verisk→Verisk · v1 Corgi→Verisk · source `event-0118` / target `event-0119` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `None` · possession frames source `{'A': 5}` / target `{'B': 41}`
- `W5-E010` @ 1826.0s · gold Corgi→Corgi · v1 Verisk→Corgi · source `event-0033` / target `event-0034` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `None` · possession frames source `{'B': 11}` / target `{'A': 97}`
- `W5-E011` @ 1831.0s · gold Corgi→Corgi · v1 Verisk→Verisk · source `event-0035` / target `event-0036` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `UPSTREAM_IDENTITY_ERROR` · possession frames source `{'B': 6}` / target `{'B': 12}`
- `W6-E013` @ 1958.0s · gold Verisk→Verisk · v1 Corgi→Verisk · source `event-0068` / target `event-0069` · actor `UPSTREAM_IDENTITY_ERROR` · receiver `None` · possession frames source `{'A': 8}` / target `{'B': 6}`

## V1 vs V2 matched attribution

| Metric | V1 | V2 |
| --- | ---: | ---: |
| Actor accuracy | 53.33% | 53.33% |
| Receiver accuracy | 70.00% | 70.00% |
| Unknown actor | 0 | 0 |
| Unknown receiver | 0 | 0 |

## Actor confusion matrix

- V1: `{'Corgi -> Corgi': 7, 'Corgi -> Verisk': 4, 'Verisk -> Corgi': 10, 'Verisk -> Verisk': 9}`
- V2: `{'Corgi -> Corgi': 7, 'Corgi -> Verisk': 4, 'Verisk -> Corgi': 10, 'Verisk -> Verisk': 9}`

## Receiver confusion matrix

- V1: `{'Corgi -> Corgi': 8, 'Corgi -> Verisk': 2, 'Verisk -> Corgi': 7, 'Verisk -> Verisk': 13}`
- V2: `{'Corgi -> Corgi': 8, 'Corgi -> Verisk': 2, 'Verisk -> Corgi': 7, 'Verisk -> Verisk': 13}`

## Aggregate team share

| Metric | Gold | V1 | V2 |
| --- | ---: | ---: |
| Attempts | 48 | 97 | 97 |
| Corgi attempts | 20 | 49 | 49 |
| Verisk attempts | 28 | 48 | 48 |
| Unknown attempts | 0 | 0 | 0 |
| Corgi share | 41.67% | 50.52% | 50.52% |
| Verisk share | 58.33% | 49.48% | 49.48% |
| Completion rate | 63.83% | 50.52% | 50.52% |
- Corgi share delta vs gold: V1 `8.85 pp`; V2 `8.85 pp`.
- Verisk share delta vs gold: V1 `-8.85 pp`; V2 `-8.85 pp`.

## False-positive team distribution

- V1: `{'Corgi': 32, 'Verisk': 35, 'unknown': 0}`
- V2: `{'Corgi': 32, 'Verisk': 35, 'unknown': 0}`

## Outcome/pass-type side effects

- `{'pass_type_changes': 0, 'outcome_changes': 0, 'matched_outcome_corrected': 0, 'matched_outcome_worsened': 0, 'changed_candidate_rows': 0}`

## Changed candidates

- Source changes: **0**; target changes: **0**; corrected matched: **0**; worsened matched: **0**; unknown introduced: **0**.

## Identity-vs-team-error decomposition

- `{'actor:UPSTREAM_IDENTITY_ERROR': 14, 'receiver:UPSTREAM_IDENTITY_ERROR': 9}`

## Decision

**UPSTREAM_IDENTITY_BLOCKER**. Candidate generation remains Pass Policy v1; no pass suppression, thresholds, contacts or restart logic changed.
