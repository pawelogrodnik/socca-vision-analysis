# Open-play Pass Policy v2 — evaluation-only comparison

## Active-play mask

- Selected window duration: **450.0s**
- Active-play scored duration: **342.4s**
- Excluded duration: **107.6s**

| Window | Active-play duration | Excluded game-state ranges | Restart ranges |
| --- | ---: | --- | --- |
| W1 | 71.0s | — | 40.0–42.0, 61.0–63.0 |
| W2 | 53.5s | 540.0–556.5 | 555.5–557.5, 569.0–571.0, 577.0–579.0 |
| W3 | 36.0s | 1261.3–1269.3, 1282.3–1290.3, 1294.3–1310.3 | 1268.3–1270.3, 1274.8–1276.8, 1289.3–1291.3, 1309.3–1311.3, 1319.3–1321.3 |
| W4 | 65.0s | 1392.0–1397.0 | 1387.0–1389.0, 1396.0–1398.0, 1405.0–1407.0 |
| W5 | 45.9s | 1761.9–1784.0 | 1806.5–1809.5, 1822.0–1824.0, 1834.0–1836.0 |
| W6 | 71.0s | — | 1947.0–1949.0, 1971.0–1973.0 |

## Primary active open-play metrics

| Metric | Gold | V1 | V2 |
| --- | ---: | ---: | ---: |
| Attempts | 46 | 97 | 89 |
| Corgi attempts | 20 | 49 | 44 |
| Verisk attempts | 26 | 48 | 45 |
| Unknown attempts | 0 | 0 | 0 |
| Corgi share | 43.48% | 50.52% | 49.44% |
| Verisk share | 56.52% | 49.48% | 50.56% |
| Completion rate | 63.04% | 50.52% | 50.56% |
| Corgi completion | 55.00% | 51.02% | 50.00% |
| Verisk completion | 69.23% | 50.00% | 51.11% |
| Completed | 29 | 49 | 45 |
| Failed | 17 | 48 | 44 |

## Event-level diagnostics (secondary)

| Metric | V1 | V2 |
| --- | ---: | ---: |
| Precision | 30.93% | 29.21% |
| Recall | 65.22% | 56.52% |
| F1 | 41.96% | 38.52% |
| Matched | 30 | 26 |
| Missed | 16 | 20 |
| False positives | 67 | 63 |
| Outcome accuracy | 53.33% | 53.85% |
| Actor-team accuracy | 53.33% | 57.69% |
| Receiver-team accuracy | 70.00% | 69.23% |

## Aggregate error versus gold

| Metric | V1 | V2 |
| --- | ---: | ---: |
| Corgi count delta / error | +29 / +145.00% | +24 / +120.00% |
| Corgi share delta | +7.04 pp | +5.96 pp |
| Corgi completion delta | -3.98 pp | -5.00 pp |
| Verisk count delta / error | +22 / +84.62% | +19 / +73.08% |
| Verisk share delta | -7.04 pp | -5.96 pp |
| Verisk completion delta | -19.23 pp | -18.12 pp |
| Overall completion delta | -12.52 pp | -12.48 pp |

## Active-play false positives

| Category | V1 | V2 |
| --- | ---: | ---: |
| SHOT_AS_PASS | 10 | 7 |
| INTERVENTION_AS_PASS | 8 | 8 |
| CONTROL_AS_PASS | 2 | 2 |
| CONTEST_AS_PASS | 2 | 2 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 3 |
| UNMATCHED_PASS_CANDIDATE | 41 | 41 |

## V2 policy effect

- Decision: **V2_MIXED**
- Rejection reasons: `{'local_contact_scramble': 8}`

### Newly lost gold passes

- W1-E006 @ 25.00s · b05 → player 1 · INTERCEPTED · v1 `pass-0013` · v2 `['local_contact_scramble']`
- W4-E005 @ 1355.00s · b04/B? → b12 · INTERCEPTED · v1 `pass-0060` · v2 `['local_contact_scramble']`
- W4-E006 @ 1356.00s · Andrzej → Piotrek · COMPLETED · v1 `pass-0061` · v2 `['local_contact_scramble']`
- W4-E009 @ 1359.00s · b07 → b02 · COMPLETED · v1 `pass-0064` · v2 `['local_contact_scramble']`

### Newly matched gold passes

- None.

### Removed false positives

- `pass-0003` @ 1786.07s · SHOT_AS_PASS · `['local_contact_scramble']`
- `pass-0004` @ 1786.17s · SHOT_AS_PASS · `['local_contact_scramble']`
- `pass-0005` @ 1786.27s · SHOT_AS_PASS · `['local_contact_scramble']`
- `pass-0007` @ 1786.77s · AMBIGUOUS_ACTION_AS_PASS · `['local_contact_scramble']`

## Secondary / non-blocking

Restart and dead-ball behavior are deliberately outside the v2 decision.

| Metric | V1 | V2 |
| --- | ---: | ---: |
| Whole-window pass attempts | 112 | 104 |
| Restart pass attempts | 4 | 4 |
| Dead-ball pass candidates | 5 | 5 |
