# Contact / Action baseline — current pipeline vs Goldset v1

## Executive summary

This is a read-only measurement of regenerated automatic candidates. Historical manual reviews are not used to turn a miss into a hit.

### PASS

- Gold attempts: **66**
- Matched / missed: **36 / 30**
- Unmatched pass candidates: **76**
- Precision / recall / F1: **32.1% / 54.5% / 40.5%**
- Broad outcome accuracy: **48.6%** (35 scorable matches)

### CONTACT

- Strict-anchor recall: **78.1%** (75 / 96)

### RESTART / confusion / dead ball

- Restart recall: **33.3%**
- Shot-as-pass: **6 / 11**
- Dead-ball spurious pass candidates: **5**
- Dead-ball spurious contact candidates: **18**

## Results by window

| Window | Gold passes | Matched | Missed | Pass candidates | Unmatched candidates |
| --- | ---: | ---: | ---: | ---: | ---: |
| W1 | 13 | 11 | 2 | 29 | 18 |
| W2 | 8 | 5 | 3 | 17 | 12 |
| W3 | 11 | 3 | 8 | 14 | 11 |
| W4 | 16 | 11 | 5 | 17 | 6 |
| W5 | 8 | 5 | 3 | 23 | 18 |
| W6 | 10 | 1 | 9 | 12 | 11 |

## Missed gold passes

- 00:21.0 · `W1-E005` · b06 → b05 · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 00:42.0 · `W1-E013` · b04 → b02 · UNKNOWN · `PASS_CONSTRUCTION_MISS`
- 09:16.5 · `W2-E001` · b07 → b05 · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 09:38.0 · `W2-E011` · player 1 → b06 · COMPLETED · `CONTACT_MISS`
- 10:15.0 · `W2-E019` · b04 → b07 · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 21:09.3 · `W3-E001` · Mati GK → Piotrek · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 21:13.3 · `W3-E002` · Piotrek → Roman · MISCONTROLLED · `PASS_CONSTRUCTION_MISS`
- 21:15.8 · `W3-E004` · B goalkeeper → b01/B? · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 21:30.3 · `W3-E007` · Mati GK → Roman · MISCONTROLLED · `CONTACT_MISS`
- 21:50.3 · `W3-E011` · b07 → b12 · MISCONTROLLED · `PASS_CONSTRUCTION_MISS`
- 22:00.3 · `W3-E013` · Mati GK → Piotrek · COMPLETED · `CONTACT_MISS`
- 22:03.3 · `W3-E014` · Piotrek → Roman · INTERCEPTED · `PASS_CONSTRUCTION_MISS`
- 22:11.3 · `W3-E017` · b08 → b12 · MISCONTROLLED · `PASS_CONSTRUCTION_MISS`
- 22:37.0 · `W4-E007` · Piotrek → Mateusz · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 22:38.0 · `W4-E008` · Mateusz → Paweł · INTERCEPTED · `PASS_CONSTRUCTION_MISS`
- 23:26.0 · `W4-E016` · Mati GK → Piotrek · COMPLETED · `CONTACT_MISS`
- 23:33.0 · `W4-E017` · Piotrek → Andrzej · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 23:35.0 · `W4-E018` · Andrzej → Piotrek · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 30:08.0 · `W5-E004` · B goalkeeper → b05 · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 30:17.0 · `W5-E006` · b04 → B defender/B03 · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 30:35.0 · `W5-E013` · B goalkeeper → b02 · COMPLETED · `CONTACT_MISS`
- 31:53.0 · `W6-E001` · b13 → b04 · INTERCEPTED · `CONTACT_MISS`
- 32:28.0 · `W6-E009` · Mati GK → Mateusz · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 32:30.0 · `W6-E010` · Mateusz → Kuba · INTERCEPTED · `PASS_CONSTRUCTION_MISS`
- 32:34.0 · `W6-E011` · b13 → b03 · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 32:35.0 · `W6-E012` · b03 → b13 · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 32:42.0 · `W6-E016` · Kuba → Krzysiek · COMPLETED · `PASS_CONSTRUCTION_MISS`
- 32:43.0 · `W6-E017` · Krzysiek → Roman · MISCONTROLLED · `PASS_CONSTRUCTION_MISS`
- 32:52.0 · `W6-E018` · B goalkeeper → b13 · COMPLETED · `CONTACT_MISS`
- 33:06.5 · `W6-E019` · b13 → b04 · COMPLETED · `PASS_CONSTRUCTION_MISS`

## False-positive passes

- 00:10.5 · `pass-0002` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E002
- 00:12.3 · `pass-0004` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E002
- 00:13.8 · `pass-0005` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E003
- 00:15.5 · `pass-0007` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E003
- 00:16.9 · `pass-0008` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E004
- 00:22.0 · `pass-0010` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E005
- 00:31.8 · `pass-0019` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E010
- 00:37.0 · `pass-0329` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E011
- 00:43.5 · `pass-0021` · SHOT_AS_PASS · nearest: W1-S001
- 00:45.5 · `pass-0023` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E015
- 00:46.4 · `pass-0024` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E015
- 00:46.9 · `pass-0025` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E015
- 00:47.6 · `pass-0026` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E015
- 00:59.8 · `pass-0330` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E016
- 01:02.2 · `pass-0029` · UNMATCHED_PASS_CANDIDATE · nearest: W1-E016
- 01:03.2 · `pass-0030` · INTERVENTION_AS_PASS · nearest: W1-E017
- 01:03.6 · `pass-0031` · INTERVENTION_AS_PASS · nearest: W1-E017
- 01:07.2 · `pass-0032` · CONTEST_AS_PASS · nearest: W1-E018
- 09:14.1 · `pass-0334` · DEAD_BALL_PASS · nearest: W2-E001
- 09:19.7 · `pass-0173` · UNMATCHED_PASS_CANDIDATE · nearest: W2-E002
- 09:23.3 · `pass-0177` · INTERVENTION_AS_PASS · nearest: W2-E004
- 09:23.7 · `pass-0178` · INTERVENTION_AS_PASS · nearest: W2-E004
- 09:24.1 · `pass-0179` · INTERVENTION_AS_PASS · nearest: W2-E005
- 09:48.9 · `pass-0185` · SHOT_AS_PASS · nearest: W2-S001
- 09:50.6 · `pass-0186` · AMBIGUOUS_ACTION_AS_PASS · nearest: W2-E017
- 09:53.3 · `pass-0187` · INTERVENTION_AS_PASS · nearest: W2-E018
- 09:53.7 · `pass-0188` · SHOT_AS_PASS · nearest: W2-S002
- 09:54.6 · `pass-0189` · SHOT_AS_PASS · nearest: W2-S002
- 10:07.7 · `pass-0190` · UNMATCHED_PASS_CANDIDATE · nearest: W2-E019
- 10:11.1 · `pass-0191` · UNMATCHED_PASS_CANDIDATE · nearest: W2-E019
- 21:10.8 · `pass-0025` · UNMATCHED_PASS_CANDIDATE · nearest: W3-E001
- 21:37.6 · `pass-0029` · DEAD_BALL_PASS · nearest: W3-E010
- 21:39.0 · `pass-0030` · DEAD_BALL_PASS · nearest: W3-E010
- 21:54.1 · `pass-0031` · UNMATCHED_PASS_CANDIDATE · nearest: W3-E012
- 21:54.6 · `pass-0032` · UNMATCHED_PASS_CANDIDATE · nearest: W3-E012
- 22:04.5 · `pass-0034` · INTERVENTION_AS_PASS · nearest: W3-E015
- 22:05.9 · `pass-0035` · UNMATCHED_PASS_CANDIDATE · nearest: W3-E015
- 22:10.0 · `pass-0039` · UNMATCHED_PASS_CANDIDATE · nearest: W3-E017
- 22:15.7 · `pass-0040` · UNMATCHED_PASS_CANDIDATE · nearest: W3-E018
- 22:16.0 · `pass-0041` · UNMATCHED_PASS_CANDIDATE · nearest: W3-E018
- 22:16.3 · `pass-0042` · UNMATCHED_PASS_CANDIDATE · nearest: W3-E018
- 22:31.5 · `pass-0055` · CONTEST_AS_PASS · nearest: W4-E002
- 22:40.7 · `pass-0065` · UNMATCHED_PASS_CANDIDATE · nearest: W4-E010
- 22:45.1 · `pass-0068` · UNMATCHED_PASS_CANDIDATE · nearest: W4-E012
- 23:16.4 · `pass-0073` · DEAD_BALL_PASS · nearest: W4-E015
- 23:36.6 · `pass-0076` · UNMATCHED_PASS_CANDIDATE · nearest: W4-E019
- 23:41.6 · `pass-0079` · UNMATCHED_PASS_CANDIDATE · nearest: W4-E019
- 29:22.6 · `pass-0001` · DEAD_BALL_PASS · nearest: W5-S001
- 29:45.1 · `pass-0002` · AMBIGUOUS_ACTION_AS_PASS · nearest: W5-E001
- 29:46.1 · `pass-0003` · SHOT_AS_PASS · nearest: W5-S002
- 29:46.2 · `pass-0004` · SHOT_AS_PASS · nearest: W5-S002
- 29:46.3 · `pass-0005` · SHOT_AS_PASS · nearest: W5-S002
- 29:46.8 · `pass-0007` · AMBIGUOUS_ACTION_AS_PASS · nearest: W5-E002
- 29:47.3 · `pass-0008` · AMBIGUOUS_ACTION_AS_PASS · nearest: W5-E002
- 29:48.4 · `pass-0009` · CONTROL_AS_PASS · nearest: W5-E003
- 29:51.1 · `pass-0010` · SHOT_AS_PASS · nearest: W5-S003
- 30:12.4 · `pass-0011` · UNMATCHED_PASS_CANDIDATE · nearest: W5-E005
- 30:19.6 · `pass-0014` · UNMATCHED_PASS_CANDIDATE · nearest: W5-E008
- 30:19.8 · `pass-0015` · UNMATCHED_PASS_CANDIDATE · nearest: W5-E008
- 30:20.0 · `pass-0016` · UNMATCHED_PASS_CANDIDATE · nearest: W5-E008
- 30:20.7 · `pass-0017` · UNMATCHED_PASS_CANDIDATE · nearest: W5-E008
- 30:30.8 · `pass-0021` · UNMATCHED_PASS_CANDIDATE · nearest: W5-E011
- 30:32.0 · `pass-0023` · INTERVENTION_AS_PASS · nearest: W5-E012
- 30:32.8 · `pass-0024` · SHOT_AS_PASS · nearest: W5-S004
- 30:33.0 · `pass-0025` · SHOT_AS_PASS · nearest: W5-S004
- 32:22.4 · `pass-0039` · UNMATCHED_PASS_CANDIDATE · nearest: W6-E009
- 32:36.6 · `pass-0040` · UNMATCHED_PASS_CANDIDATE · nearest: W6-E013
- 32:38.2 · `pass-0042` · UNMATCHED_PASS_CANDIDATE · nearest: W6-E013
- 32:40.0 · `pass-0099` · INTERVENTION_AS_PASS · nearest: W6-E014
- 32:40.9 · `pass-0043` · CONTROL_AS_PASS · nearest: W6-E015
- 32:44.5 · `pass-0045` · UNMATCHED_PASS_CANDIDATE · nearest: W6-E017
- 32:44.6 · `pass-0046` · UNMATCHED_PASS_CANDIDATE · nearest: W6-E017
- 32:44.7 · `pass-0047` · UNMATCHED_PASS_CANDIDATE · nearest: W6-E017
- 32:47.6 · `pass-0048` · UNMATCHED_PASS_CANDIDATE · nearest: W6-E018
- 32:48.2 · `pass-0049` · UNMATCHED_PASS_CANDIDATE · nearest: W6-E018
- 32:58.0 · `pass-0050` · UNMATCHED_PASS_CANDIDATE · nearest: W6-E018

## Outcome errors

- 00:03.5 · `W1-E001`: gold completed_pass vs system failed_pass
- 00:15.0 · `W1-E003`: gold completed_pass vs system failed_pass
- 00:25.0 · `W1-E006`: gold failed_pass vs system completed_pass
- 00:28.0 · `W1-E009`: gold failed_pass vs system completed_pass
- 00:41.0 · `W1-E012`: gold completed_pass vs system failed_pass
- 09:25.0 · `W2-E006`: gold failed_pass vs system completed_pass
- 21:18.0 · `W3-E005`: gold failed_pass vs system completed_pass
- 22:32.5 · `W4-E003`: gold completed_pass vs system failed_pass
- 22:36.0 · `W4-E006`: gold completed_pass vs system failed_pass
- 22:41.0 · `W4-E010`: gold completed_pass vs system failed_pass
- 23:08.0 · `W4-E013`: gold completed_pass vs system failed_pass
- 23:17.0 · `W4-E015`: gold completed_pass vs system failed_pass
- 23:38.0 · `W4-E019`: gold failed_pass vs system completed_pass
- 30:18.5 · `W5-E007`: gold failed_pass vs system completed_pass
- 30:23.0 · `W5-E009`: gold completed_pass vs system failed_pass
- 30:26.0 · `W5-E010`: gold completed_pass vs system failed_pass
- 30:31.0 · `W5-E011`: gold failed_pass vs system completed_pass
- 32:38.0 · `W6-E013`: gold completed_pass vs system failed_pass

## Identity context

- 00:15.0 · `W1-E003` · team correct=False · source slot=A05
- 00:18.0 · `W1-E004` · team correct=True · source slot=B08
- 09:20.0 · `W2-E002` · team correct=True · source slot=B07
- 09:41.0 · `W2-E013` · team correct=True · source slot=A07
- 22:33.3 · `W4-E004` · team correct=False · source slot=A08
- 22:35.0 · `W4-E005` · team correct=False · source slot=A07
- 22:36.0 · `W4-E006` · team correct=False · source slot=B12
- 22:46.5 · `W4-E012` · team correct=True · source slot=B07
- 30:23.0 · `W5-E009` · team correct=True · source slot=A07
- 32:38.0 · `W6-E013` · team correct=False · source slot=A05

## Shot/pass confusion

- 00:43.5 · `shot-review-c2fbf99d-3ca7-401f-a3c7-989f427785c1` · pass-0021 (-0.02s)
- 09:48.5 · `shot-review-0c9a417f-6d11-4b06-9a1d-b33dcc4f2990` · pass-0185 (+0.39s)
- 09:54.3 · `shot-review-49a57e87-d29a-488e-850e-8d2841f52f64` · pass-0188 (-0.61s), pass-0189 (+0.29s)
- 29:46.1 · `shot-review-962984be-75c2-4a09-af97-5f461c0c0b63` · pass-0002 (-0.96s), pass-0003 (-0.03s), pass-0004 (+0.07s), pass-0005 (+0.17s), pass-0007 (+0.67s)
- 29:52.0 · `shot-review-f0b70547-3dbb-4b75-bdd4-12ce05b73abe` · pass-0010 (-0.86s)
- 30:33.5 · `shot-review-96b0fd71-0dd8-4381-96c1-b7dacf6c2a9c` · pass-0024 (-0.71s), pass-0025 (-0.51s)

## Dead-ball leakage

- W2 NOT_IN_PLAY 09:00.0–09:16.5: contacts 2, events 2, passes 1
- W3 NOT_IN_PLAY 21:01.3–21:09.3: contacts 1, events 1, passes 0
- W3 GK_HOLD 21:22.3–21:30.3: contacts 0, events 0, passes 0
- W3 NOT_IN_PLAY 21:34.3–21:50.3: contacts 8, events 8, passes 2
- W4 NOT_IN_PLAY 23:12.0–23:17.0: contacts 3, events 3, passes 1
- W5 NOT_IN_PLAY 29:21.9–29:44.0: contacts 4, events 4, passes 1

## Contact hard negative

- 00:46.0 · W1-E015: automatic Corgi/Mateusz-like contact candidates = 1

## Top failure categories

- `PASS_CONSTRUCTION_MISS`: **23**
- `PASS_OUTCOME_ERROR`: **18**
- `CORRECT`: **10**
- `CONTACT_MISS`: **7**
- `ACTOR_TEAM_ERROR`: **5**
- `RESTART_ATTRIBUTION_ERROR`: **2**
- `RECEIVER_TEAM_ERROR`: **1**

This report is descriptive baseline evidence only; it does not change production behavior.
