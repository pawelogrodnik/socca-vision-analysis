# Reviewed identity regression validation: `668ae4c0`

**Verdict:** mixed

## Evidence matrix

- Direct same-tracklet slot regressions: 0 events / 0 tracklets / 0 observations.
- Direct same-tracklet slot losses: 161 events / 66 tracklets / 2035 observations.
- Resolver slot losses: 1786 observations; frame-uniqueness demotions: 249; operator slot removals: 0.
- Direct Team-U regressions: 0 events / 0 tracklets / 0 observations.
- Suspected upstream fragmentation indications: 1 candidate subjects (not counted as definitive core switches).

## Operator-binding case studies

- **Mati GK**: anchor A07; named coverage 75.7%; roster_binding_fragmentation (DEFINITE).
- **Przemek**: anchor A02; named coverage 98.4%; roster_binding_fragmentation (DEFINITE).
- **Andrzej**: anchor A01; named coverage 99.8%; roster_binding_fragmentation (DEFINITE).
- **Roman**: anchor A04; named coverage 100.0%; operator_binding_complete (STRONG_INDICATION).
- **Piotrek**: anchor A06; named coverage 99.2%; roster_binding_fragmentation (DEFINITE).
- **Paweł**: anchor A03; named coverage 100.0%; operator_binding_complete (STRONG_INDICATION).
- **Krzysiek**: anchor A05; named coverage 60.3%; roster_binding_fragmentation (DEFINITE).

## Roman re-anchor gap

- Needs visual/operator confirmation: `True`.
- A common global slot is a stability observation, not proof that the two fragments are the same real-world player.

## BEFORE -> AFTER validation

- **Mati GK**: anchor A07; named coverage 43.2% -> 75.7%; first true unnamed frame {'frame': 11, 'time_sec': 0.367, 'tracklet_id': '100014:1', 'candidate_subject_id': 'shadow-a-96cc41d1ae1012f6', 'reviewed_label': 'A? !'}; parallel unnamed fragment none; remaining [{'value': 'conflicted:duplicate_canonical_player_in_frame,duplicate_stable_slot_in_frame', 'observations': 9, 'ratio': 1.0}].
- **Przemek**: anchor A02; named coverage 5.9% -> 98.4%; first true unnamed frame {'frame': 93, 'time_sec': 3.103, 'tracklet_id': '100003:1', 'candidate_subject_id': 'shadow-a-5f37fa5b8708d172', 'reviewed_label': 'A? !'}; parallel unnamed fragment none; remaining [{'value': 'conflicted:duplicate_canonical_player_in_frame,duplicate_stable_slot_in_frame', 'observations': 44, 'ratio': 1.0}].
- **Andrzej**: anchor A01; named coverage 11.9% -> 99.8%; first true unnamed frame {'frame': 308, 'time_sec': 10.277, 'tracklet_id': '100001:1', 'candidate_subject_id': 'shadow-a-10667a7322e106a2', 'reviewed_label': 'A? !'}; parallel unnamed fragment none; remaining [{'value': 'conflicted:duplicate_canonical_player_in_frame,duplicate_stable_slot_in_frame', 'observations': 6, 'ratio': 1.0}].
- **Roman**: anchor A04; named coverage 35.5% -> 100.0%; first true unnamed frame none; parallel unnamed fragment none; remaining operator_binding_complete.
- **Piotrek**: anchor A06; named coverage 91.0% -> 99.2%; first true unnamed frame {'frame': 444, 'time_sec': 14.815, 'tracklet_id': '100013:1', 'candidate_subject_id': 'shadow-a-23512ec6d60dae41', 'reviewed_label': 'A? !'}; parallel unnamed fragment none; remaining [{'value': 'conflicted:duplicate_canonical_player_in_frame,duplicate_stable_slot_in_frame', 'observations': 4, 'ratio': 1.0}].
- **Paweł**: anchor A03; named coverage 0.0% -> 100.0%; first true unnamed frame none; parallel unnamed fragment none; remaining operator_binding_complete.
- **Krzysiek**: anchor A05; named coverage 28.8% -> 60.3%; first true unnamed frame {'frame': 1050, 'time_sec': 35.035, 'tracklet_id': '100027:2', 'candidate_subject_id': 'shadow-a-67f03ba45cc96a56', 'reviewed_label': 'A? !'}; parallel unnamed fragment {'frame': 1036, 'time_sec': 34.568, 'tracklet_id': '100027:2', 'candidate_subject_id': 'shadow-a-67f03ba45cc96a56', 'reviewed_label': 'A? !'}; remaining [{'value': 'conflicted:conflicting_canonical_stable_sources', 'observations': 1618, 'ratio': 1.0}].
- Team-U direct reviewed regressions: 915 -> 0 observations.
- AFTER slot losses: resolver 1786; frame uniqueness 249; operator removals 0.

## Source safety

- Source artifacts unchanged: `True`.

## Recommendations

- Preserve a valid global stable slot/team through reviewed resolution unless direct contradictory evidence is recorded.
- Bind an operator-confirmed roster name to the verified stable slot only after showing any unresolved cross-fragment evidence.
