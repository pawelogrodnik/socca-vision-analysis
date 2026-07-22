# Jersey Number Identity Anchors

## Status

```text
SUPPLEMENT TO task-requests/PLAYER_IDENTITY_STABILIZATION_ROADMAP.md
SHADOW-FIRST / HIGH-CONFIDENCE IDENTITY EVIDENCE
N0-N5 IMPLEMENTED IN SHADOW
NOT READY FOR AUTOMATIC CANDIDATE OR PRODUCTION ASSIGNMENTS
```

Baseline reviewed:

```text
d1ef613e1916a503f22607a035288edc432b95f9
```

This document reflects the actual N0-N5 implementation and the targeted hard3m benchmark added after the first N5 easy90 run.

The implementation direction is correct, but the feature is not yet complete as an automatic jersey-number recognizer or as a candidate-assignment source. N0-N5 currently prove that manually reviewed number evidence can be converted into conservative roster suggestions and propagated through existing safe lineage without mutating candidate or production identity.

The feature does **not** yet prove that the system can automatically read jersey numbers from video with sufficient precision.

---

# 1. Domain assumptions

The current product assumptions are:

- a non-empty jersey number is unique within one team;
- the same number may exist in Team A and Team B;
- not every player has a number;
- a player with a number uses the same number throughout the match;
- several players may use plain white shirts without a number;
- `Team A + 92` may map uniquely to Paweł;
- `Team A + 15` may map uniquely to Piotrek.

A trusted unique number is stronger identity evidence than generic appearance/ReID, but it never bypasses team, temporal, spatial, structural or lineage safety constraints.

---

# 2. Current implementation status

## N0 — roster number registry

Implemented:

- optional jersey number per roster player;
- explicit confirmed absence of a number;
- normalization of one-, two- and three-digit values;
- uniqueness validation within a team;
- duplicate number conflicts disable trust;
- the same number across different teams is allowed;
- detected evidence never mutates roster configuration.

Current status:

```text
IMPLEMENTED / SHADOW-ONLY / CORRECT DIRECTION
```

## N1 — crop evidence and operator audit

Implemented:

- reliable anchor crop filtering;
- torso ROI extraction;
- visual audit gallery;
- four evidence states;
- low-quality crops remain excluded;
- no recognizer output means `number_unreadable`, never `number_absent`.

Current states:

```text
number_confirmed
number_absent
number_unreadable
number_conflict
```

Important limitation:

```text
A calibrated automatic jersey-number recognizer/OCR is not implemented.
```

Current positive evidence is operator-reviewed. Therefore N1 currently validates the evidence contract and human audit workflow, not automatic number recognition.

Current status:

```text
INFRASTRUCTURE IMPLEMENTED
AUTOMATIC RECOGNIZER PENDING
```

## N2 — tracklet and subject consensus

Implemented:

- deterministic consensus per tracklet;
- deterministic consensus per candidate subject;
- minimum three independent reads by default;
- minimum frame separation;
- confidence threshold;
- same-team unique roster lookup;
- strong contradictory reads produce a conflict;
- goldset evaluator reports precision, recall, false positives and identity false assignments.

Easy90 operator-reviewed result:

```text
expected numbered subjects: 17
strong consensus subjects:    4
correct strong consensus:      4
false positives:               0
identity false assignments:    0
precision:                   1.0
recall:                 0.235294
```

Interpretation:

```text
precision signal: promising
coverage: low
sample size: too small for activation
```

## N3 — whole-subject review suggestion

Implemented:

- strong number consensus may populate the recommended roster player;
- number suggestion is visible in the whole-subject review card;
- weak reads do not create a recommendation;
- disagreement with another roster recommendation creates `jersey_number_roster_conflict`;
- conflict blocks one-click confirmation;
- operator review remains required.

Current status:

```text
IMPLEMENTED / SHADOW REVIEW ASSISTANCE
```

## N4 — gated assignment plan

Implemented:

- explicit activation request;
- benchmark gate;
- lineage gate against review and report artifacts;
- structural blocker checks;
- strictly eligible shadow candidates;
- no write to candidate or production identity;
- `automatic_assignments = 0` even when a row is eligible.

Current status:

```text
SHADOW PLAN IMPLEMENTED
GATE CONTRACT REQUIRES HARDENING BEFORE CANDIDATE USE
```

## N5 — strict propagation through existing lineage

Implemented:

- number-confirmed seed tracklet;
- propagation only through explicit existing transition edges;
- candidate/timeline subject membership validation;
- path and hop audit;
- contradictory number blocking;
- no new edge from number similarity;
- no tracklet merge;
- no cross-subject propagation;
- no automatic assignment.

Blocked cases include:

```text
uncertain_transition
cross_production_transition
temporal overlap
weak ReID-only edge
candidate/timeline tracklet mismatch
team mismatch
structural subject conflict
contradictory number evidence
```

Current status:

```text
IMPLEMENTED IN SHADOW
POSITIVE INTRA-SUBJECT COVERAGE BENEFIT DEMONSTRATED ON ONE TARGET
NOT ACTIVATION-READY
```

---

# 3. Benchmark results

## 3.1. Easy90 N0-N4

Local frozen artifacts:

```text
backend/storage/benchmarks/player_identity/n0-n4-jersey-number-easy90-20260721-v2
backend/storage/benchmarks/player_identity/n0-n4-jersey-number-easy90-20260722-goldset-evaluated
```

Reported result:

```text
evidence rows:             437
reliable rows:             331
rejected rows:             106
reliable Team A crops:     133
numbered goldset subjects:  17
strong consensus:            4
correct:                     4
precision:                 1.0
recall:               0.235294
```

This validates conservative consensus on a small manually reviewed sample. It does not validate an automatic recognizer.

## 3.2. Easy90 N5

Local frozen artifacts:

```text
backend/storage/benchmarks/player_identity/n5-jersey-number-propagation-easy90-20260722-v1
```

Reported result:

```text
seed subjects:              3
seed tracklets:             3
propagated tracklets:       0
cross-subject propagation:  0
automatic assignments:      0
```

Every eligible easy90 subject contained only one tracklet. This run validated non-expansion safety but could not measure propagation benefit.

## 3.3. Targeted hard3m N5 benchmark

Selection:

```text
backend/storage/benchmarks/player_identity/n5-jersey-number-hard3m-targeted-20260722-v1
```

Reviewed result:

```text
backend/storage/benchmarks/player_identity/n5-jersey-number-hard3m-targeted-reviewed-20260722-v1
```

Reported selection:

```text
multi-tracklet Team A subjects: 7
seed tracklets:                 7
selected crops:                25
final reliable audit crops:    22
hidden target tracklets:        8
confirmed number reads:         5
number_absent reads:            5
number_unreadable reads:       12
```

Reported evaluation:

```text
strong consensus subjects:              1
eligible hidden target tracklets:        1
matched eligible hidden targets:         1
eligible_target_recall:                1.0
unexpected propagated tracklets:         0
cross-subject propagations:              0
automatic assignments:                   0
safety passed:                         true
```

This is the first real evidence that N5 can increase coverage inside a multi-tracklet candidate subject while preserving current safety rules.

It is still only one positive eligible target. It is not sufficient for candidate activation.

---

# 4. Architectural decision for N5

N5 is defined as:

```text
trusted number seed
→ strict accepted existing lineage edge
→ another tracklet inside the same candidate subject
```

N5 is **not** cross-subject identity resolution.

Current behavior is intentionally:

```text
cross-subject edge
→ blocked
```

This avoids allowing one OCR mistake or one weak graph edge to spread a roster identity across independent subjects.

A future cross-subject number-assisted resolver, if justified, must be a separate milestone with separate goldset, constraints and activation gate. It must start as ranking/review assistance, not automatic merge.

---

# 5. Semantics of number evidence

## `number_confirmed`

Several independent, high-quality observations support one trusted number that exists uniquely in the same-team roster.

## `number_absent`

A clean jersey surface is visible and no number is present.

`number_absent` never identifies a specific player when multiple players have no number.

## `number_unreadable`

Blur, scale, body orientation, crop quality, obstruction or missing recognizer output prevents a reliable read.

```text
no OCR result != number_absent
```

## `number_conflict`

Examples:

```text
same tracklet contains trusted 92 and trusted 15
subject assigned to player 92 contains trusted 15
number maps to Team A but identity evidence says Team B
number exists but is duplicated in the same-team roster
```

A strong number conflict is structural evidence and blocks automatic/candidate assignment until reviewed or remediated.

---

# 6. Consensus contract

Default strong consensus requires:

```text
known trusted team
+ unique trusted roster number
+ at least 3 independent high-confidence reads
+ reads separated in time
+ reliable crop quality
+ no strong competing number
+ no structural identity conflict
```

A single clean crop may be displayed as supporting evidence but cannot create strong consensus.

Example:

```text
92: 3 high-quality reads
15: 1 low-confidence partial read
→ possible consensus 92
→ weak competing evidence remains auditable
```

Two strong competing numbers must produce `number_conflict`.

---

# 7. Required hardening before candidate activation

The current implementation is not fundamentally wrong. The following items are required to make its activation contract correct and measurable.

## N5.1 — canonical structural blockers

Create one shared canonical set used by:

```text
P1.20A promotion safety
whole-subject review
N4 assignment plan
N5 propagation
candidate apply
```

Current N4 and N5 blocker sets are not identical. A subject must not be `strictly_eligible` in N4 and only later become structurally blocked in N5 because the modules use different flag lists.

Required blocker coverage includes at least:

```text
cross_production_transition
merges_production_subjects
parallel_distant_observation
parallel_roster_candidate_conflict
roster_identity_conflict
structural_identity_conflict
team_switch
temporal_overlap_conflict
uncertain_transition
jersey_number_roster_conflict
cross_team_evidence
```

## N5.2 — harden N4 benchmark gate

N4 currently must not pass solely because:

```text
identity_false_assignments == 0
```

The accepted benchmark gate must require:

```text
identity_false_assignments == 0
false_positive == 0
precision == 1.0
minimum reviewed numbered subjects reached
minimum reviewed no-number subjects reached
minimum reviewed unreadable/negative subjects reached
at least one held-out match passed
```

The exact minimum sample sizes should be chosen before activation and recorded in the report, not tuned after seeing the result.

## N5.3 — full lineage validation

N5 currently records digests of its direct inputs. Before candidate use it must also validate that the artifacts belong to the same lineage.

Required checks:

```text
assignment consensus digest matches current consensus
assignment review digest matches current review artifact
consensus evidence digest matches current evidence
consensus roster digest matches current roster
candidate digest matches expected candidate artifact
timeline digest matches expected timeline artifact
algorithm/version/parameter digests are present
```

A stale assignment plan with a new timeline or evidence artifact must produce:

```text
status = blocked
reason = stale_jersey_number_lineage
```

## N5.4 — separate seed provenance

Do not combine number evidence and operator confirmation into one undifferentiated seed set.

Report separately:

```text
number_seed_tracklet_ids
operator_confirmed_tracklet_ids
number_propagated_tracklet_ids
operator_inherited_tracklet_ids
```

This is necessary to measure how much coverage was added by the jersey number itself.

A whole-subject operator confirmation may mark membership as trusted, but it must not inflate the reported N5 propagation gain.

## N5.5 — automatic recognizer calibration

Implement and evaluate a recognizer only after the current visual audit contract remains stable.

Recommended flow:

```text
reliable torso/back crop
→ number-region proposal or constrained torso ROI
→ digit/number recognizer
→ calibrated confidence
→ per-crop evidence
```

Do not run unconstrained OCR on the whole frame or full player crop.

Evaluate separately:

```text
readability classification precision
number/digit accuracy
numbered-player false positive rate
no-number-player false positive rate
subject consensus precision
subject consensus coverage
identity false assignments
```

The highest-priority negative case is:

```text
plain shirt without a number
→ recognizer hallucinates 92
→ wrong Paweł assignment
```

## N5.6 — version lightweight benchmark artifacts

`backend/storage/**` is ignored by Git, so local benchmark summaries cannot be independently inspected from the repository.

Keep large crops and videos local, but commit lightweight artifacts under a tracked path, for example:

```text
backend/benchmarks/player_identity/jersey-number/easy90-v1/
backend/benchmarks/player_identity/jersey-number/hard3m-targeted-v1/
```

Each tracked benchmark should include:

```text
benchmark_manifest.json
goldset_summary.json
consensus_report.json
assignment_gate_report.json
propagation_report.json
targeted_evaluation.json
source commit and input digests
```

No raw video or crop images need to be committed.

## N5.7 — CI

Add lightweight CI coverage for:

```text
roster uniqueness
no-read vs no-number semantics
consensus conflicts
N4 benchmark gate
stale lineage blocking
N5 safe-edge propagation
N5 unsafe-edge blocking
targeted hidden-tracklet evaluation
determinism and input immutability
```

Heavy crop/model evaluation may remain manual, but frozen JSON contract tests should run in CI.

---

# 8. Activation sequence

The recommended sequence from the current state is:

```text
N5.1  canonical blockers
N5.2  benchmark gate hardening and negative goldset
N5.3  full lineage validation
N5.4  seed provenance metrics
N5.5  automatic recognizer calibration
N5.6  tracked lightweight benchmark reports
N5.7  CI contract coverage
N5.8  held-out multi-match shadow benchmark
N5.9  controlled candidate-only integration
```

Do not wait until production apply to evaluate the recognizer and propagation. Run them in shadow against full-match operator benchmarks from P1.22.

---

# 9. Candidate-only activation gate

Jersey-number evidence may affect candidate identity only after all of the following pass:

```text
trusted same-team unique roster number
multi-frame strong consensus
0 identity false assignments in accepted benchmarks
0 false positives in accepted benchmarks
negative no-number sample included
held-out match included
fresh full lineage
0 structural blockers
0 contradictory trusted number
0 cross-team evidence
0 temporal overlap conflict
0 parallel distant observation
0 unexpected propagated target
production artifacts unchanged
```

The first activation must remain candidate-only.

It may:

```text
add a candidate roster suggestion
mark a candidate tracklet as number-propagated
reduce operator review priority
```

It may not:

```text
write production assignments
publish player stats
merge independent subjects
create a new lineage edge from number similarity
```

---

# 10. Metrics

Report separately:

## Recognition metrics

```text
reliable crops evaluated
readable-number precision
number accuracy
digit accuracy
false number on plain shirt
number_absent precision
number_unreadable rate
```

## Consensus metrics

```text
numbered goldset subjects
strong consensus subjects
subject precision
subject recall
false positives
identity false assignments
number conflicts
```

## Propagation metrics

```text
number seed tracklets
operator seed tracklets
eligible hidden targets
matched hidden targets
unexpected propagated tracklets
eligible target recall
coverage frames added
subjects with real propagation benefit
cross-subject propagations
automatic assignments
```

## Product metrics

```text
additional subjects correctly suggested
additional tracklets correctly resolved
manual review cards avoided
manual review time saved
false roster assignments caused by number evidence
```

High precision remains more important than coverage.

---

# 11. Acceptance criteria

## Implemented in shadow

- [x] roster supports optional unique number per team;
- [x] duplicate same-team numbers disable trust;
- [x] `number_absent` differs from `number_unreadable`;
- [x] no recognizer output remains unreadable;
- [x] only reliable torso crops enter the visual audit;
- [x] consensus requires multiple independent reads;
- [x] strong conflicting numbers block consensus;
- [x] number maps only to a unique player in the same team;
- [x] players without numbers are not identified by missing OCR;
- [x] strong number consensus may assist whole-subject review;
- [x] N4 remains shadow-only and writes no assignments;
- [x] N5 uses only existing explicit lineage edges;
- [x] N5 does not create cross-subject edges;
- [x] N5 blocks uncertain, overlapping, cross-production and weak ReID-only transitions;
- [x] targeted hard3m benchmark demonstrated one correct hidden-tracklet propagation;
- [x] no unexpected propagation occurred in the targeted reported sample;
- [x] production identity, stats and heatmaps remain unchanged.

## Required before candidate activation

- [ ] one canonical structural blocker set is shared across N4/N5 and promotion safety;
- [ ] N4 gate includes false positives and negative/no-number coverage;
- [ ] N5 validates full lineage instead of only recording input digests;
- [ ] number and operator seed provenance are reported separately;
- [ ] automatic recognizer is calibrated on front/back torso crops;
- [ ] no-number hallucination rate is explicitly measured;
- [ ] lightweight benchmark reports are versioned in Git;
- [ ] relevant frozen contract tests run in CI;
- [ ] at least one held-out match passes with zero identity false assignments;
- [ ] more than one positive multi-tracklet propagation is evaluated;
- [ ] candidate integration remains reversible and production-safe.

## Required before production use

- [ ] candidate-only integration demonstrates reduced review work;
- [ ] no false automatic roster assignment is observed in the accepted multi-match benchmark;
- [ ] no unexpected propagation is observed;
- [ ] production promotion uses the main roadmap transaction, backup and rollback contract;
- [ ] feature readiness clearly distinguishes identity, stats and unavailable optional inputs.

---

# 12. Anti-goals

Do not:

- treat one crop as automatic identity;
- treat missing OCR as a shirt without a number;
- identify a no-number player from absence alone;
- use number similarity to create or merge tracklets;
- propagate through uncertain or overlapping transitions;
- open cross-subject propagation inside N5;
- tune thresholds only on easy90 or the reviewed hard3m sample;
- call `1/1` eligible target recall production validation;
- activate N4 because one small goldset has no identity false assignment;
- publish candidate identity or stats;
- commit raw videos or large crop galleries solely to version benchmark results.

---

# 13. Final interpretation

The current N0-N5 feature is a **correct conservative shadow prototype**.

The latest targeted hard3m benchmark fixes the largest evidence gap from the first N5 run: it demonstrates that a trusted number seed can recover a hidden tracklet inside a multi-tracklet subject.

The evidence is still limited:

```text
1 eligible positive target
1 matched target
0 unexpected targets
```

Therefore the correct project status is:

```text
architecture: valid
shadow safety: promising
intra-subject coverage benefit: demonstrated on a minimal sample
automatic recognition: not implemented
candidate activation: blocked pending hardening and held-out benchmark
production activation: not allowed
```

The next work should prioritize gate correctness, recognizer evaluation and multi-match evidence rather than adding another generic ReID score or enabling cross-subject propagation.