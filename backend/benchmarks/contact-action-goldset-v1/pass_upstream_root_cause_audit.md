# Pass Upstream Root-Cause Audit

## Executive summary

This is a read-only GO / NO-GO audit. It regenerates current Pass Policy v1 evidence, then uses the goldset only to label and select errors for inspection.
- Decision: **NO_SHARED_FIX_FOUND**.
- Recommendation: **PARK AUTOMATIC PASS DEVELOPMENT**.

## Current pass quality

- Gold / automatic / matched / missed / false positives: **48 / 97 / 30 / 18 / 67**.

## Missed-pass root causes

- `W1-E005` · W1 · **CONTACT_GENERATION** · source `9c7485e4` @ `21.0`s
- `W1-E013` · W1 · **PASS_PAIR_CONSTRUCTION** · source `9c7485e4` @ `42.0`s
- `W2-E019` · W2 · **CONTACT_GENERATION** · source `9c7485e4` @ `615.0`s
- `W3-E002` · W3 · **PASS_PAIR_CONSTRUCTION** · source `6d8fc20c` @ `117.0`s
- `W3-E014` · W3 · **PASS_PAIR_CONSTRUCTION** · source `6d8fc20c` @ `167.0`s
- `W3-E017` · W3 · **PASS_PAIR_CONSTRUCTION** · source `6d8fc20c` @ `175.0`s
- `W4-E007` · W4 · **PASS_PAIR_CONSTRUCTION** · source `6d8fc20c` @ `200.7`s
- `W4-E008` · W4 · **RELEASE_POLICY** · source `6d8fc20c` @ `201.7`s
- `W4-E017` · W4 · **PASS_PAIR_CONSTRUCTION** · source `6d8fc20c` @ `256.7`s
- `W4-E018` · W4 · **CONTACT_GENERATION** · source `6d8fc20c` @ `258.7`s
- `W5-E006` · W5 · **UNKNOWN** · source `5e62625e` @ `55.122`s
- `W6-E001` · W6 · **CONTACT_GENERATION** · source `5e62625e` @ `151.122`s
- `W6-E010` · W6 · **PASS_PAIR_CONSTRUCTION** · source `5e62625e` @ `188.122`s
- `W6-E011` · W6 · **PASS_PAIR_CONSTRUCTION** · source `5e62625e` @ `192.122`s
- `W6-E012` · W6 · **PASS_PAIR_CONSTRUCTION** · source `5e62625e` @ `193.122`s
- `W6-E016` · W6 · **RELEASE_POLICY** · source `5e62625e` @ `200.122`s
- `W6-E017` · W6 · **RELEASE_POLICY** · source `5e62625e` @ `201.122`s
- `W6-E019` · W6 · **CONTACT_GENERATION** · source `5e62625e` @ `224.622`s

## Team-attribution upstream root causes

- `W1-E002` · W1 · **UNKNOWN** · source `9c7485e4` @ `10.644`s
- `W1-E003` · W1 · **UNKNOWN** · source `9c7485e4` @ `14.348`s
- `W1-E006` · W1 · **UNKNOWN** · source `9c7485e4` @ `25.659`s
- `W1-E006` · W1 · **UNKNOWN** · source `9c7485e4` @ `25.993`s
- `W1-E010` · W1 · **UNKNOWN** · source `9c7485e4` @ `31.265`s
- `W2-E006` · W2 · **UNKNOWN** · source `9c7485e4` @ `565.165`s
- `W2-E006` · W2 · **UNKNOWN** · source `9c7485e4` @ `569.903`s
- `W3-E005` · W3 · **UNKNOWN** · source `6d8fc20c` @ `120.654`s
- `W3-E005` · W3 · **UNKNOWN** · source `6d8fc20c` @ `124.291`s
- `W3-E009` · W3 · **UNKNOWN** · source `6d8fc20c` @ `137.337`s
- `W3-E009` · W3 · **UNKNOWN** · source `6d8fc20c` @ `140.44`s
- `W3-E016` · W3 · **UNKNOWN** · source `6d8fc20c` @ `170.938`s
- `W3-E016` · W3 · **UNKNOWN** · source `6d8fc20c` @ `171.805`s
- `W4-E003` · W4 · **UNKNOWN** · source `6d8fc20c` @ `197.064`s
- `W4-E004` · W4 · **UNKNOWN** · source `6d8fc20c` @ `197.064`s
- `W4-E004` · W4 · **UNKNOWN** · source `6d8fc20c` @ `198.198`s
- `W4-E005` · W4 · **UNKNOWN** · source `6d8fc20c` @ `198.999`s
- `W4-E006` · W4 · **UNKNOWN** · source `6d8fc20c` @ `199.199`s
- `W4-E010` · W4 · **UNKNOWN** · source `6d8fc20c` @ `204.471`s
- `W5-E010` · W5 · **UNKNOWN** · source `5e62625e` @ `64.004`s
- `W5-E011` · W5 · **UNKNOWN** · source `5e62625e` @ `69.009`s
- `W5-E011` · W5 · **UNKNOWN** · source `5e62625e` @ `69.71`s
- `W6-E013` · W6 · **UNKNOWN** · source `5e62625e` @ `195.849`s

## False-positive sample root causes

- Deterministic sample: category caps (shot 6, intervention 5, control 2, contest 2, ambiguous 3) plus a W1–W6 round-robin generic sample capped at 6.
- `pass-0021` · W1 · **SHOT_REBOUND_CHAIN** · source `9c7485e4` @ `43.477`s
- `pass-0185` · W2 · **SHOT_REBOUND_CHAIN** · source `9c7485e4` @ `588.889`s
- `pass-0188` · W2 · **SHOT_REBOUND_CHAIN** · source `9c7485e4` @ `593.694`s
- `pass-0189` · W2 · **SHOT_REBOUND_CHAIN** · source `9c7485e4` @ `594.595`s
- `pass-0003` · W5 · **SHOT_REBOUND_CHAIN** · source `5e62625e` @ `24.193`s
- `pass-0004` · W5 · **SHOT_REBOUND_CHAIN** · source `5e62625e` @ `24.293`s
- `pass-0030` · W1 · **INTERVENTION_CHAIN** · source `9c7485e4` @ `63.163`s
- `pass-0031` · W1 · **INTERVENTION_CHAIN** · source `9c7485e4` @ `63.63`s
- `pass-0177` · W2 · **INTERVENTION_CHAIN** · source `9c7485e4` @ `563.33`s
- `pass-0178` · W2 · **INTERVENTION_CHAIN** · source `9c7485e4` @ `563.697`s
- `pass-0179` · W2 · **INTERVENTION_CHAIN** · source `9c7485e4` @ `564.131`s
- `pass-0009` · W5 · **CONTINUED_CONTROL_FRAGMENTATION** · source `5e62625e` @ `26.496`s
- `pass-0043` · W6 · **CONTINUED_CONTROL_FRAGMENTATION** · source `5e62625e` @ `199.019`s
- `pass-0032` · W1 · **UNKNOWN** · source `9c7485e4` @ `67.234`s
- `pass-0055` · W4 · **UNKNOWN** · source `6d8fc20c` @ `195.229`s
- `pass-0186` · W2 · **UNKNOWN** · source `9c7485e4` @ `590.591`s
- `pass-0002` · W5 · **UNKNOWN** · source `5e62625e` @ `23.259`s
- `pass-0007` · W5 · **UNKNOWN** · source `5e62625e` @ `24.894`s
- `pass-0002` · W1 · **UNKNOWN** · source `9c7485e4` @ `10.511`s
- `pass-0173` · W2 · **UNKNOWN** · source `9c7485e4` @ `559.726`s
- `pass-0025` · W3 · **UNKNOWN** · source `6d8fc20c` @ `114.515`s
- `pass-0065` · W4 · **UNKNOWN** · source `6d8fc20c` @ `204.404`s
- `pass-0011` · W5 · **UNKNOWN** · source `5e62625e` @ `50.522`s
- `pass-0039` · W6 · **UNKNOWN** · source `5e62625e` @ `180.499`s

## Cross-population error families

| Root cause | MISS | TEAM | FP sample | Total |
| --- | ---: | ---: | ---: | ---: |
| UNKNOWN | 1 | 23 | 11 | 35 |
| PASS_PAIR_CONSTRUCTION | 9 | 0 | 0 | 9 |
| SHOT_REBOUND_CHAIN | 0 | 0 | 6 | 6 |
| CONTACT_GENERATION | 5 | 0 | 0 | 5 |
| INTERVENTION_CHAIN | 0 | 0 | 5 | 5 |
| RELEASE_POLICY | 3 | 0 | 0 | 3 |
| CONTINUED_CONTROL_FRAGMENTATION | 0 | 0 | 2 | 2 |

## Contact-player association findings

- `{'wrong_possession_owner_demonstrated': 0, 'misses_with_explicit_pair_skip': 9, 'fp_samples_with_contact_fragmentation': 0, 'conclusion': 'No contact-player selector defect is asserted unless a closer opposite-team production candidate is present in the trace.'}`

## Contact-fragmentation findings

- `{'multi_candidate_clusters': 21, 'true_only': 4, 'false_only': 9, 'mixed': 8, 'miss_pair_construction_count': 9, 'fp_fragmentation_asserted': 0, 'conclusion': 'Cluster density remains non-selective because mixed clusters contain genuine passes; no suppression recommendation follows.'}`

## Identity findings

- `{'team_error_count': 23, 'wrong_possession_owner_demonstrated': 0, 'unresolved_first_divergence': 23, 'operator_decision_contradiction': 0, 'conclusion': 'Where contact, canonical slot and local possession agree on a team that conflicts with gold, this audit cannot distinguish a wrong physical-player association from an upstream identity slot without new human identity evidence.'}`

## Ball-track findings

- `{'misses_with_no_nearby_effective_ball_frame': 0, 'ball_track_not_causal_for_remaining_misses': 18, 'conclusion': 'No ball-detector change is proposed; effective ball tracks are only marked causal where the trace contains no local usable frame.'}`

## Shared-fix candidates

- None. No measured cause has a bounded, cross-population, gold-independent production fix.

## Go / No-Go

**NO_SHARED_FIX_FOUND**.
**RECOMMENDATION: PARK AUTOMATIC PASS DEVELOPMENT.** The audit found heterogeneous failures and no safe shared production change; existing experimental artifacts remain retained.
