# Reviewed identity regression validation: `668ae4c0`

**Verdict:** reviewed_regression_resolved

## Evidence matrix

- Direct same-tracklet slot regressions: 0 events / 0 tracklets / 0 observations.
- Direct same-tracklet slot losses: 151 events / 64 tracklets / 249 observations.
- Resolver slot losses: 0 observations; frame-uniqueness demotions: 249; operator slot removals: 0.
- Direct Team-U regressions: 0 events / 0 tracklets / 0 observations.
- Suspected upstream fragmentation indications: 3 candidate subjects (not counted as definitive core switches).

## Operator-binding case studies

- **Mati GK**: anchor A07; named coverage 75.7%; roster_binding_fragmentation (DEFINITE).
- **Przemek**: anchor A02; named coverage 98.4%; roster_binding_fragmentation (DEFINITE).
- **Andrzej**: anchor A01; named coverage 99.8%; roster_binding_fragmentation (DEFINITE).
- **Roman**: anchor A04; named coverage 100.0%; operator_binding_complete (STRONG_INDICATION).
- **Piotrek**: anchor A06; named coverage 99.2%; roster_binding_fragmentation (DEFINITE).
- **Paweł**: anchor A03; named coverage 100.0%; operator_binding_complete (STRONG_INDICATION).
- **Krzysiek**: anchor A05; named coverage 100.0%; operator_binding_complete (STRONG_INDICATION).

## Roman re-anchor gap

- Needs visual/operator confirmation: `True`.
- A common global slot is a stability observation, not proof that the two fragments are the same real-world player.

## Frame-level canonical ownership

- Global/stable derived artifact integrity: `exact_mirror`.
- `100027:2` → `A03` at [[1218, 1457], [1464, 1464], [1470, 1471], [1473, 1474], [1476, 2691]]; rendered Pawel @ [[1218, 1457], [1464, 1464], [1470, 1471], [1473, 1474], [1476, 2691]].
- `100027:2` → `A05` at [[1050, 1057]]; rendered Krzysiek @ [[1050, 1057]].
- `100061:1` → `A08` at [[557, 557], [562, 563], [565, 565], [567, 567], [569, 569], [571, 576], [578, 578]]; rendered A08 ! @ [[557, 557], [562, 563], [565, 565], [567, 567], [569, 569], [571, 576], [578, 578]].
- `100061:1` → `A09` at [[681, 723]]; rendered A09 ! @ [[681, 723]].

## BEFORE -> AFTER validation

- **Mati GK**: anchor A07; named coverage 75.7% -> 75.7%; first true unnamed frame {'frame': 11, 'time_sec': 0.367, 'tracklet_id': '100014:1', 'candidate_subject_id': 'shadow-a-96cc41d1ae1012f6', 'reviewed_label': 'A? !'}; parallel unnamed fragment none; remaining [{'value': 'conflicted:duplicate_canonical_player_in_frame,duplicate_stable_slot_in_frame', 'observations': 9, 'ratio': 1.0}].
- **Przemek**: anchor A02; named coverage 98.4% -> 98.4%; first true unnamed frame {'frame': 93, 'time_sec': 3.103, 'tracklet_id': '100003:1', 'candidate_subject_id': 'shadow-a-5f37fa5b8708d172', 'reviewed_label': 'A? !'}; parallel unnamed fragment none; remaining [{'value': 'conflicted:duplicate_canonical_player_in_frame,duplicate_stable_slot_in_frame', 'observations': 44, 'ratio': 1.0}].
- **Andrzej**: anchor A01; named coverage 99.8% -> 99.8%; first true unnamed frame {'frame': 308, 'time_sec': 10.277, 'tracklet_id': '100001:1', 'candidate_subject_id': 'shadow-a-10667a7322e106a2', 'reviewed_label': 'A? !'}; parallel unnamed fragment none; remaining [{'value': 'conflicted:duplicate_canonical_player_in_frame,duplicate_stable_slot_in_frame', 'observations': 6, 'ratio': 1.0}].
- **Roman**: anchor A04; named coverage 100.0% -> 100.0%; first true unnamed frame none; parallel unnamed fragment none; remaining operator_binding_complete.
- **Piotrek**: anchor A06; named coverage 99.2% -> 99.2%; first true unnamed frame {'frame': 444, 'time_sec': 14.815, 'tracklet_id': '100013:1', 'candidate_subject_id': 'shadow-a-23512ec6d60dae41', 'reviewed_label': 'A? !'}; parallel unnamed fragment none; remaining [{'value': 'conflicted:duplicate_canonical_player_in_frame,duplicate_stable_slot_in_frame', 'observations': 4, 'ratio': 1.0}].
- **Paweł**: anchor A03; named coverage 100.0% -> 100.0%; first true unnamed frame none; parallel unnamed fragment none; remaining operator_binding_complete.
- **Krzysiek**: anchor A05; named coverage 100.0% -> 100.0%; first true unnamed frame none; parallel unnamed fragment none; remaining operator_binding_complete.
- Team-U direct reviewed regressions: 0 -> 0 observations.
- AFTER slot losses: resolver 0; frame uniqueness 249; operator removals 0.

## Source safety

- Source artifacts unchanged: `True`.

## Recommendations

- Collect visual/operator confirmation for suspected cross-slot candidate fragments before changing core stitching.
- Keep the reviewed layer read-only while separating slot stability from real-world roster correctness.
