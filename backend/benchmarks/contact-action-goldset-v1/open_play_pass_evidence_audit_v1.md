# Open-play Pass Evidence Audit

## Executive summary

This is a read-only forensic audit of regenerated v1 candidates. Gold labels are applied only after candidate generation and never enter runtime pass statistics.
- Conclusion: **EVIDENCE_SUFFICIENT_FOR_V3**
- Recommended next experiment: **Collapse repeated candidate pairs around one short contact cluster**

## Current error budget

- Gold open-play passes: **48**
- Matched / missed / false positives: **30 / 18 / 67**

## Team-share bias decomposition

- Missed by gold team: `{'Corgi': 9, 'Verisk': 9}`
- False positives by automatic team: `{'Corgi': 32, 'Verisk': 35}`

| Gold actor → automatic actor | Count |
| --- | ---: |
| Corgi -> Corgi | 7 |
| Corgi -> Verisk | 4 |
| Verisk -> Corgi | 10 |
| Verisk -> Verisk | 9 |

## Completion bias decomposition

| Gold outcome → automatic outcome | Count |
| --- | ---: |
| completed_pass -> completed_pass | 13 |
| completed_pass -> failed_pass | 7 |
| failed_pass -> completed_pass | 7 |
| failed_pass -> failed_pass | 3 |

- False-positive outcomes: `{'completed_pass': 29, 'failed_pass': 38}`
- False-positive outcomes by team: `{'Corgi': {'completed_pass': 14, 'failed_pass': 18}, 'Verisk': {'completed_pass': 15, 'failed_pass': 20}, 'unknown': {}}`

## Top separating evidence

| Feature | True median | All-FP median | True n | FP n |
| --- | ---: | ---: | ---: | ---: |
| trajectory.sampled_frames | 21.0 | 15.0 | 30 | 67 |
| derived.free_frame_count | 15.0 | 11.0 | 30 | 67 |
| trajectory.ball_path_distance_m | 6.1815 | 4.329 | 30 | 67 |
| distance_m | 5.144 | 3.782 | 30 | 67 |
| trajectory.ball_displacement_m | 5.091 | 3.85 | 30 | 67 |
| release.source_clearance_m | 5.6015 | 4.957 | 30 | 67 |
| receiver.min_distance_m | 0.5635 | 1.14 | 30 | 67 |
| trajectory.mean_ball_speed_mps | 9.274 | 9.82 | 30 | 67 |

## Evidence distributions

### duration_sec

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.1 | 0.1976 | 0.4505 | 0.6675 | 1.3602 | 2.7024 | 4.738 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.066 | 0.1 | 0.267 | 0.467 | 1.084 | 2.4028 | 4.404 |
| SHOT_AS_PASS | 10 | 0 | 0.067 | 0.0967 | 0.1673 | 0.35 | 0.7085 | 4.3743 | 4.404 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.167 | 0.237 | 0.267 | 0.3 | 0.6088 | 1.805 | 3.604 |
| CONTROL_AS_PASS | 2 | 0 | 1.001 | 1.0544 | 1.1345 | 1.268 | 1.4015 | 1.4816 | 1.535 |
| CONTEST_AS_PASS | 2 | 0 | 1.134 | 1.154 | 1.184 | 1.234 | 1.284 | 1.314 | 1.334 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.234 | 0.2841 | 0.3593 | 0.551 | 1.1763 | 2.0317 | 2.602 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.066 | 0.1 | 0.267 | 0.501 | 1.001 | 2.135 | 4.171 |

### distance_m

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 1.791 | 2.5515 | 3.354 | 5.144 | 10.7502 | 12.4786 | 28.458 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.613 | 1.7896 | 2.564 | 3.782 | 9.3375 | 12.9968 | 37.998 |
| SHOT_AS_PASS | 10 | 0 | 1.57 | 1.6861 | 1.9305 | 4.656 | 10.069 | 15.4529 | 21.203 |
| INTERVENTION_AS_PASS | 8 | 0 | 2.309 | 2.7213 | 2.9272 | 3.4505 | 4.7188 | 11.1104 | 24.927 |
| CONTROL_AS_PASS | 2 | 0 | 5.532 | 5.8959 | 6.4417 | 7.3515 | 8.2613 | 8.8071 | 9.171 |
| CONTEST_AS_PASS | 2 | 0 | 5.824 | 6.0756 | 6.453 | 7.082 | 7.711 | 8.0884 | 8.34 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 1.927 | 2.2039 | 2.6193 | 2.9585 | 3.4763 | 4.2129 | 4.704 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.613 | 1.61 | 2.461 | 3.649 | 9.587 | 12.696 | 37.998 |

### displacement_m

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 30 | — | — | — | — | — | — | — |
| ALL_FALSE_POSITIVES | 67 | 67 | — | — | — | — | — | — | — |
| SHOT_AS_PASS | 10 | 10 | — | — | — | — | — | — | — |
| INTERVENTION_AS_PASS | 8 | 8 | — | — | — | — | — | — | — |
| CONTROL_AS_PASS | 2 | 2 | — | — | — | — | — | — | — |
| CONTEST_AS_PASS | 2 | 2 | — | — | — | — | — | — | — |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 4 | — | — | — | — | — | — | — |
| UNMATCHED_PASS_CANDIDATE | 41 | 41 | — | — | — | — | — | — | — |

### confidence

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.2625 | 0.2625 | 0.35 | 0.35 | 0.5625 | 0.7657 | 0.8036 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.2519 | 0.2625 | 0.2625 | 0.35 | 0.35 | 0.5952 | 0.7948 |
| SHOT_AS_PASS | 10 | 0 | 0.2519 | 0.2614 | 0.2625 | 0.35 | 0.35 | 0.3712 | 0.5625 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.2625 | 0.2625 | 0.2625 | 0.3063 | 0.35 | 0.4137 | 0.5625 |
| CONTROL_AS_PASS | 2 | 0 | 0.2625 | 0.294 | 0.3412 | 0.42 | 0.4987 | 0.5459 | 0.5774 |
| CONTEST_AS_PASS | 2 | 0 | 0.2625 | 0.2625 | 0.2625 | 0.2625 | 0.2625 | 0.2625 | 0.2625 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.2625 | 0.2625 | 0.2625 | 0.3063 | 0.412 | 0.5237 | 0.5982 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.2607 | 0.2625 | 0.2625 | 0.35 | 0.5625 | 0.75 | 0.7948 |

### release.duration_sec

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.1 | 0.1976 | 0.4505 | 0.6675 | 1.3602 | 2.7024 | 4.738 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.066 | 0.1 | 0.267 | 0.467 | 1.084 | 2.4028 | 4.404 |
| SHOT_AS_PASS | 10 | 0 | 0.067 | 0.0967 | 0.1673 | 0.35 | 0.7085 | 4.3743 | 4.404 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.167 | 0.237 | 0.267 | 0.3 | 0.6088 | 1.805 | 3.604 |
| CONTROL_AS_PASS | 2 | 0 | 1.001 | 1.0544 | 1.1345 | 1.268 | 1.4015 | 1.4816 | 1.535 |
| CONTEST_AS_PASS | 2 | 0 | 1.134 | 1.154 | 1.184 | 1.234 | 1.284 | 1.314 | 1.334 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.234 | 0.2841 | 0.3593 | 0.551 | 1.1763 | 2.0317 | 2.602 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.066 | 0.1 | 0.267 | 0.501 | 1.001 | 2.135 | 4.171 |

### release.source_clearance_m

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 1.409 | 2.8221 | 4.2752 | 5.6015 | 11.931 | 14.879 | 35.12 |
| ALL_FALSE_POSITIVES | 67 | 0 | 1.565 | 2.3576 | 3.6075 | 4.957 | 9.581 | 13.769 | 39.766 |
| SHOT_AS_PASS | 10 | 0 | 2.345 | 2.444 | 2.7635 | 5.4335 | 10.4183 | 15.2604 | 22.581 |
| INTERVENTION_AS_PASS | 8 | 0 | 3.379 | 3.7815 | 4.428 | 4.6225 | 4.979 | 12.4379 | 28.533 |
| CONTROL_AS_PASS | 2 | 0 | 6.39 | 6.8076 | 7.434 | 8.478 | 9.522 | 10.1484 | 10.566 |
| CONTEST_AS_PASS | 2 | 0 | 8.779 | 8.8288 | 8.9035 | 9.028 | 9.1525 | 9.2272 | 9.277 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 3.46 | 3.5005 | 3.5613 | 4.183 | 5.5565 | 6.9704 | 7.913 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 1.565 | 2.096 | 2.644 | 4.957 | 10.278 | 13.515 | 39.766 |

### release.immediate_contested_frames

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 | 3.0 | 3.0 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 2.0 | 3.0 | 3.0 |
| SHOT_AS_PASS | 10 | 0 | 0.0 | 0.0 | 0.0 | 1.5 | 3.0 | 3.0 | 3.0 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.25 | 2.0 | 2.0 |
| CONTROL_AS_PASS | 2 | 0 | 0.0 | 0.2 | 0.5 | 1.0 | 1.5 | 1.8 | 2.0 |
| CONTEST_AS_PASS | 2 | 0 | 0.0 | 0.2 | 0.5 | 1.0 | 1.5 | 1.8 | 2.0 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.0 | 0.0 | 0.0 | 1.5 | 3.0 | 3.0 | 3.0 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 1.0 | 2.0 | 3.0 |

### release.controlled_by_source_frames

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 4.0 |
| ALL_FALSE_POSITIVES | 67 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 3.0 |
| SHOT_AS_PASS | 10 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.2 | 3.0 |
| INTERVENTION_AS_PASS | 8 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| CONTROL_AS_PASS | 2 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| CONTEST_AS_PASS | 2 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

### release.controlled_by_target_frames

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| ALL_FALSE_POSITIVES | 67 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| SHOT_AS_PASS | 10 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| INTERVENTION_AS_PASS | 8 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| CONTROL_AS_PASS | 2 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| CONTEST_AS_PASS | 2 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

### trajectory.sampled_frames

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 4.0 | 6.9 | 14.5 | 21.0 | 41.75 | 82.0 | 143.0 |
| ALL_FALSE_POSITIVES | 67 | 0 | 3.0 | 4.0 | 9.0 | 15.0 | 33.5 | 73.0 | 133.0 |
| SHOT_AS_PASS | 10 | 0 | 3.0 | 3.9 | 6.0 | 11.5 | 22.25 | 132.1 | 133.0 |
| INTERVENTION_AS_PASS | 8 | 0 | 6.0 | 8.1 | 9.0 | 10.0 | 19.25 | 55.1 | 109.0 |
| CONTROL_AS_PASS | 2 | 0 | 31.0 | 32.6 | 35.0 | 39.0 | 43.0 | 45.4 | 47.0 |
| CONTEST_AS_PASS | 2 | 0 | 35.0 | 35.6 | 36.5 | 38.0 | 39.5 | 40.4 | 41.0 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 8.0 | 9.5 | 11.75 | 17.5 | 36.25 | 61.9 | 79.0 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 3.0 | 4.0 | 9.0 | 16.0 | 31.0 | 65.0 | 126.0 |

### trajectory.ball_path_distance_m

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 1.741 | 2.3516 | 3.3185 | 6.1815 | 11.3577 | 24.4809 | 53.444 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.857 | 1.8842 | 2.453 | 4.329 | 10.5245 | 16.8726 | 65.963 |
| SHOT_AS_PASS | 10 | 0 | 1.57 | 1.6906 | 2.4845 | 5.1255 | 14.9998 | 22.0646 | 65.963 |
| INTERVENTION_AS_PASS | 8 | 0 | 2.351 | 2.7507 | 2.94 | 4.178 | 5.445 | 17.4441 | 43.671 |
| CONTROL_AS_PASS | 2 | 0 | 6.419 | 6.724 | 7.1815 | 7.944 | 8.7065 | 9.164 | 9.469 |
| CONTEST_AS_PASS | 2 | 0 | 9.669 | 9.7503 | 9.8723 | 10.0755 | 10.2787 | 10.4007 | 10.482 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 1.941 | 2.2137 | 2.6227 | 2.976 | 6.4488 | 12.4729 | 16.489 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.857 | 1.799 | 2.287 | 3.999 | 11.419 | 13.558 | 44.075 |

### trajectory.ball_displacement_m

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 1.741 | 2.2968 | 3.273 | 5.091 | 10.7502 | 12.3282 | 28.458 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.854 | 1.6634 | 2.2935 | 3.85 | 8.8245 | 12.9968 | 37.998 |
| SHOT_AS_PASS | 10 | 0 | 1.57 | 1.6861 | 1.9305 | 4.656 | 10.069 | 14.9606 | 21.275 |
| INTERVENTION_AS_PASS | 8 | 0 | 2.309 | 2.7185 | 2.897 | 3.3595 | 4.7188 | 11.1104 | 24.927 |
| CONTROL_AS_PASS | 2 | 0 | 5.532 | 5.8959 | 6.4417 | 7.3515 | 8.2613 | 8.8071 | 9.171 |
| CONTEST_AS_PASS | 2 | 0 | 5.824 | 6.0756 | 6.453 | 7.082 | 7.711 | 8.0884 | 8.34 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 1.927 | 2.2039 | 2.6193 | 2.9585 | 3.4763 | 4.2129 | 4.704 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.854 | 1.312 | 2.237 | 3.85 | 9.587 | 12.696 | 37.998 |

### trajectory.mean_ball_speed_mps

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 1.674 | 5.39 | 7.3522 | 9.274 | 12.236 | 16.3192 | 19.919 |
| ALL_FALSE_POSITIVES | 67 | 0 | 2.663 | 4.0272 | 6.0845 | 9.82 | 16.9105 | 25.027 | 64.612 |
| SHOT_AS_PASS | 10 | 0 | 3.903 | 7.3797 | 9.0875 | 15.1075 | 23.0072 | 31.5943 | 45.403 |
| INTERVENTION_AS_PASS | 8 | 0 | 5.034 | 5.7102 | 8.805 | 10.9685 | 17.6955 | 20.4153 | 22.677 |
| CONTROL_AS_PASS | 2 | 0 | 6.169 | 6.1934 | 6.23 | 6.291 | 6.352 | 6.3886 | 6.413 |
| CONTEST_AS_PASS | 2 | 0 | 7.858 | 7.9248 | 8.025 | 8.192 | 8.359 | 8.4592 | 8.526 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 4.425 | 4.5495 | 4.7363 | 5.5885 | 7.7975 | 10.4264 | 12.179 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 2.663 | 3.454 | 4.438 | 10.928 | 16.709 | 25.21 | 64.612 |

### trajectory.ball_path_straightness

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.1075 | 0.5298 | 0.944 | 0.9812 | 0.994 | 1.0 | 1.0 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.2853 | 0.4716 | 0.85 | 0.975 | 0.9975 | 0.9995 | 1.0011 |
| SHOT_AS_PASS | 10 | 0 | 0.3225 | 0.4445 | 0.6553 | 0.9719 | 0.9965 | 0.9996 | 1.0 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.4734 | 0.5416 | 0.8793 | 0.987 | 0.9986 | 0.9989 | 0.9994 |
| CONTROL_AS_PASS | 2 | 0 | 0.8618 | 0.8725 | 0.8885 | 0.9152 | 0.9418 | 0.9578 | 0.9685 |
| CONTEST_AS_PASS | 2 | 0 | 0.6023 | 0.6216 | 0.6506 | 0.6989 | 0.7473 | 0.7763 | 0.7956 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.2853 | 0.4963 | 0.8128 | 0.9908 | 0.9946 | 0.9978 | 1.0 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.3985 | 0.5502 | 0.8775 | 0.9698 | 0.9979 | 0.9995 | 1.0011 |

### receiver.min_distance_m

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.018 | 0.1558 | 0.3442 | 0.5635 | 0.986 | 1.3995 | 1.788 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.085 | 0.3212 | 0.5375 | 1.14 | 1.52 | 1.6922 | 1.799 |
| SHOT_AS_PASS | 10 | 0 | 0.13 | 0.3316 | 0.6595 | 1.1695 | 1.4558 | 1.5764 | 1.607 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.576 | 0.6173 | 0.815 | 1.18 | 1.4983 | 1.5638 | 1.596 |
| CONTROL_AS_PASS | 2 | 0 | 0.693 | 0.6992 | 0.7085 | 0.724 | 0.7395 | 0.7488 | 0.755 |
| CONTEST_AS_PASS | 2 | 0 | 1.142 | 1.2007 | 1.2887 | 1.4355 | 1.5823 | 1.6703 | 1.729 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.478 | 0.5098 | 0.5575 | 1.0905 | 1.603 | 1.6138 | 1.621 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.085 | 0.264 | 0.439 | 0.988 | 1.444 | 1.709 | 1.799 |

### receiver.confidence

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.5 | 0.5 | 0.5 | 0.75 | 0.7827 | 0.8152 | 0.8663 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.4966 | 0.5 | 0.5 | 0.5 | 0.7601 | 0.7962 | 0.8277 |
| SHOT_AS_PASS | 10 | 0 | 0.5 | 0.5 | 0.5 | 0.5 | 0.75 | 0.75 | 0.75 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.5 | 0.5 | 0.5 | 0.5 | 0.7564 | 0.7829 | 0.8003 |
| CONTROL_AS_PASS | 2 | 0 | 0.5 | 0.5321 | 0.5802 | 0.6605 | 0.7408 | 0.7889 | 0.821 |
| CONTEST_AS_PASS | 2 | 0 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 | 0.5 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.5 | 0.5 | 0.5 | 0.6327 | 0.7735 | 0.788 | 0.7976 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.4966 | 0.5 | 0.5 | 0.5 | 0.7666 | 0.7953 | 0.8277 |

### context.nearby_contact_count

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 1.0 | 1.9 | 2.0 | 3.0 | 4.0 | 5.1 | 6.0 |
| ALL_FALSE_POSITIVES | 67 | 0 | 1.0 | 2.0 | 2.0 | 3.0 | 3.5 | 4.0 | 6.0 |
| SHOT_AS_PASS | 10 | 0 | 1.0 | 1.9 | 2.0 | 2.5 | 4.75 | 5.1 | 6.0 |
| INTERVENTION_AS_PASS | 8 | 0 | 2.0 | 2.0 | 2.0 | 3.0 | 3.0 | 3.3 | 4.0 |
| CONTROL_AS_PASS | 2 | 0 | 3.0 | 3.1 | 3.25 | 3.5 | 3.75 | 3.9 | 4.0 |
| CONTEST_AS_PASS | 2 | 0 | 2.0 | 2.1 | 2.25 | 2.5 | 2.75 | 2.9 | 3.0 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 4.0 | 4.0 | 4.0 | 4.5 | 5.25 | 5.7 | 6.0 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 1.0 | 2.0 | 2.0 | 2.0 | 3.0 | 4.0 | 4.0 |

### context.prior_gap_sec

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 1 | 0.066 | 0.0934 | 0.133 | 0.434 | 1.001 | 1.408 | 2.335 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.033 | 0.067 | 0.1335 | 0.434 | 0.9175 | 1.8624 | 42.713 |
| SHOT_AS_PASS | 10 | 0 | 0.067 | 0.0967 | 0.1423 | 0.4005 | 0.7752 | 0.8711 | 1.502 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.1 | 0.2169 | 0.2918 | 0.417 | 1.009 | 1.6444 | 2.602 |
| CONTROL_AS_PASS | 2 | 0 | 0.2 | 0.3035 | 0.4588 | 0.7175 | 0.9763 | 1.1315 | 1.235 |
| CONTEST_AS_PASS | 2 | 0 | 1.334 | 1.561 | 1.9015 | 2.469 | 3.0365 | 3.377 | 3.604 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.067 | 0.0772 | 0.0925 | 0.1675 | 4.914 | 13.338 | 18.954 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.033 | 0.067 | 0.101 | 0.434 | 0.834 | 1.836 | 42.713 |

### context.next_gap_sec

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.034 | 0.0669 | 0.1753 | 0.4505 | 0.709 | 1.4719 | 16.916 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.067 | 0.1 | 0.2 | 0.434 | 1.185 | 4.011 | 12.146 |
| SHOT_AS_PASS | 10 | 0 | 0.067 | 0.0976 | 0.1673 | 0.3165 | 3.1285 | 5.1782 | 12.146 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.134 | 0.2502 | 0.675 | 1.084 | 1.927 | 3.9442 | 4.738 |
| CONTROL_AS_PASS | 2 | 0 | 0.101 | 0.2411 | 0.4513 | 0.8015 | 1.1518 | 1.3619 | 1.502 |
| CONTEST_AS_PASS | 2 | 0 | 0.434 | 1.2415 | 2.4528 | 4.4715 | 6.4903 | 7.7015 | 8.509 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.1 | 0.1201 | 0.1502 | 0.1835 | 0.2503 | 0.3407 | 0.401 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.067 | 0.1 | 0.267 | 0.5 | 1.001 | 2.27 | 10.511 |

### context.rapid_neighbor_gaps

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.0 | 0.0 | 0.0 | 1.0 | 1.0 | 2.0 | 3.0 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.0 | 0.0 | 0.5 | 1.0 | 2.0 | 2.0 | 3.0 |
| SHOT_AS_PASS | 10 | 0 | 0.0 | 0.0 | 0.25 | 1.0 | 2.0 | 3.0 | 3.0 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.0 | 0.7 | 1.0 | 1.0 | 2.0 | 2.3 | 3.0 |
| CONTROL_AS_PASS | 2 | 0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| CONTEST_AS_PASS | 2 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 1.0 | 1.3 | 1.75 | 2.0 | 2.0 | 2.0 | 2.0 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.0 | 0.0 | 0.0 | 1.0 | 2.0 | 2.0 | 3.0 |

### derived.free_frame_count

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.0 | 0.0 | 6.75 | 15.0 | 35.75 | 64.2 | 141.0 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.0 | 0.0 | 3.5 | 11.0 | 27.5 | 56.8 | 103.0 |
| SHOT_AS_PASS | 10 | 0 | 0.0 | 0.0 | 0.0 | 5.0 | 10.0 | 16.1 | 62.0 |
| INTERVENTION_AS_PASS | 8 | 0 | 3.0 | 3.7 | 5.5 | 7.0 | 16.5 | 49.8 | 103.0 |
| CONTROL_AS_PASS | 2 | 0 | 29.0 | 30.4 | 32.5 | 36.0 | 39.5 | 41.6 | 43.0 |
| CONTEST_AS_PASS | 2 | 0 | 31.0 | 31.4 | 32.0 | 33.0 | 34.0 | 34.6 | 35.0 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.0 | 3.3 | 8.25 | 14.5 | 18.5 | 19.4 | 20.0 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.0 | 0.0 | 5.0 | 12.0 | 28.0 | 58.0 | 93.0 |

### derived.free_frame_ratio

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.0 | 0.0 | 0.5079 | 0.7693 | 0.907 | 0.9418 | 0.986 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.0 | 0.0 | 0.2472 | 0.7634 | 0.8802 | 0.9342 | 0.971 |
| SHOT_AS_PASS | 10 | 0 | 0.0 | 0.0 | 0.0 | 0.0379 | 0.4597 | 0.7262 | 0.8333 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.4444 | 0.4833 | 0.575 | 0.7333 | 0.8495 | 0.8902 | 0.945 |
| CONTROL_AS_PASS | 2 | 0 | 0.9149 | 0.917 | 0.9201 | 0.9252 | 0.9304 | 0.9334 | 0.9355 |
| CONTEST_AS_PASS | 2 | 0 | 0.8537 | 0.8569 | 0.8617 | 0.8697 | 0.8777 | 0.8825 | 0.8857 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.0 | 0.0683 | 0.1709 | 0.537 | 0.8619 | 0.8902 | 0.9091 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.0 | 0.0 | 0.4715 | 0.7778 | 0.8889 | 0.9355 | 0.971 |

### derived.contested_frame_ratio

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.0 | 0.0 | 0.0 | 0.034 | 0.1781 | 0.4331 | 0.6667 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.1593 | 0.488 | 0.875 |
| SHOT_AS_PASS | 10 | 0 | 0.0 | 0.0 | 0.1096 | 0.3484 | 0.495 | 0.6178 | 0.7778 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.0 | 0.0 | 0.0 | 0.1024 | 0.175 | 0.2067 | 0.2222 |
| CONTROL_AS_PASS | 2 | 0 | 0.0 | 0.0043 | 0.0106 | 0.0213 | 0.0319 | 0.0383 | 0.0426 |
| CONTEST_AS_PASS | 2 | 0 | 0.0571 | 0.0611 | 0.0672 | 0.0774 | 0.0875 | 0.0936 | 0.0976 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.0 | 0.0 | 0.0 | 0.076 | 0.3014 | 0.5706 | 0.75 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0667 | 0.3333 | 0.875 |

### derived.controlled_frame_ratio

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.014 | 0.0278 | 0.0526 | 0.1027 | 0.1511 | 0.2905 | 0.5 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.015 | 0.0306 | 0.0598 | 0.1333 | 0.2222 | 0.5 | 0.6667 |
| SHOT_AS_PASS | 10 | 0 | 0.015 | 0.0288 | 0.0957 | 0.1742 | 0.3556 | 0.5167 | 0.6667 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.0183 | 0.0492 | 0.1156 | 0.2 | 0.25 | 0.3333 | 0.3333 |
| CONTROL_AS_PASS | 2 | 0 | 0.0426 | 0.0448 | 0.0481 | 0.0536 | 0.059 | 0.0623 | 0.0645 |
| CONTEST_AS_PASS | 2 | 0 | 0.0488 | 0.0496 | 0.0509 | 0.0529 | 0.055 | 0.0563 | 0.0571 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.0506 | 0.0627 | 0.0808 | 0.1223 | 0.1779 | 0.2211 | 0.25 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.0159 | 0.0308 | 0.0645 | 0.125 | 0.2222 | 0.5 | 0.6667 |

### derived.source_control_ratio

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.007 | 0.0139 | 0.0263 | 0.0478 | 0.0692 | 0.1453 | 0.25 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.0075 | 0.015 | 0.0299 | 0.0667 | 0.1111 | 0.25 | 0.3333 |
| SHOT_AS_PASS | 10 | 0 | 0.0075 | 0.0212 | 0.0479 | 0.0871 | 0.1778 | 0.2583 | 0.3333 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.0092 | 0.0246 | 0.0578 | 0.1 | 0.1111 | 0.1278 | 0.1667 |
| CONTROL_AS_PASS | 2 | 0 | 0.0213 | 0.0224 | 0.0241 | 0.0268 | 0.0295 | 0.0312 | 0.0323 |
| CONTEST_AS_PASS | 2 | 0 | 0.0244 | 0.0248 | 0.0255 | 0.0265 | 0.0276 | 0.0282 | 0.0286 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.0127 | 0.0225 | 0.0373 | 0.0612 | 0.0889 | 0.1106 | 0.125 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.0079 | 0.0154 | 0.0323 | 0.0625 | 0.1111 | 0.25 | 0.3333 |

### derived.target_control_ratio

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.007 | 0.0122 | 0.0241 | 0.0478 | 0.0692 | 0.1453 | 0.25 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.0075 | 0.0138 | 0.0299 | 0.0667 | 0.1111 | 0.25 | 0.3333 |
| SHOT_AS_PASS | 10 | 0 | 0.0075 | 0.0076 | 0.0479 | 0.0871 | 0.1778 | 0.2583 | 0.3333 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.0092 | 0.0246 | 0.0578 | 0.1 | 0.1111 | 0.1278 | 0.1667 |
| CONTROL_AS_PASS | 2 | 0 | 0.0213 | 0.0224 | 0.0241 | 0.0268 | 0.0295 | 0.0312 | 0.0323 |
| CONTEST_AS_PASS | 2 | 0 | 0.0244 | 0.0248 | 0.0255 | 0.0265 | 0.0276 | 0.0282 | 0.0286 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.0127 | 0.0225 | 0.0373 | 0.0612 | 0.0889 | 0.1106 | 0.125 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.0079 | 0.0154 | 0.0323 | 0.0625 | 0.1111 | 0.25 | 0.3333 |

### derived.unknown_frame_ratio

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0999 | 0.8947 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.4452 | 0.8667 |
| SHOT_AS_PASS | 10 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.3409 | 0.5465 | 0.7955 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.011 | 0.0367 |
| CONTROL_AS_PASS | 2 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| CONTEST_AS_PASS | 2 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.1424 | 0.3987 | 0.5696 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 | 0.3333 | 0.8667 |

### derived.source_release_to_target_gap_sec

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.1 | 0.1976 | 0.4505 | 0.6675 | 1.3602 | 2.7024 | 4.738 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.066 | 0.1 | 0.267 | 0.467 | 1.084 | 2.4028 | 4.404 |
| SHOT_AS_PASS | 10 | 0 | 0.067 | 0.0967 | 0.1673 | 0.35 | 0.7085 | 4.3743 | 4.404 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.167 | 0.237 | 0.267 | 0.3 | 0.6088 | 1.805 | 3.604 |
| CONTROL_AS_PASS | 2 | 0 | 1.001 | 1.0544 | 1.1345 | 1.268 | 1.4015 | 1.4816 | 1.535 |
| CONTEST_AS_PASS | 2 | 0 | 1.134 | 1.154 | 1.184 | 1.234 | 1.284 | 1.314 | 1.334 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.234 | 0.2841 | 0.3593 | 0.551 | 1.1763 | 2.0317 | 2.602 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.066 | 0.1 | 0.267 | 0.501 | 1.001 | 2.135 | 4.171 |

### derived.displacement_to_path_ratio

| Group | n | missing | min | p10 | p25 | median | p75 | p90 | max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| TRUE_PASS_MATCH | 30 | 0 | 0.1075 | 0.5298 | 0.944 | 0.9812 | 0.994 | 1.0 | 1.0 |
| ALL_FALSE_POSITIVES | 67 | 0 | 0.2853 | 0.4716 | 0.85 | 0.975 | 0.9975 | 0.9995 | 1.0011 |
| SHOT_AS_PASS | 10 | 0 | 0.3225 | 0.4445 | 0.6553 | 0.9719 | 0.9965 | 0.9996 | 1.0 |
| INTERVENTION_AS_PASS | 8 | 0 | 0.4734 | 0.5416 | 0.8793 | 0.987 | 0.9986 | 0.9989 | 0.9994 |
| CONTROL_AS_PASS | 2 | 0 | 0.8618 | 0.8725 | 0.8885 | 0.9152 | 0.9418 | 0.9578 | 0.9685 |
| CONTEST_AS_PASS | 2 | 0 | 0.6023 | 0.6216 | 0.6506 | 0.6989 | 0.7473 | 0.7763 | 0.7956 |
| AMBIGUOUS_ACTION_AS_PASS | 4 | 0 | 0.2853 | 0.4963 | 0.8128 | 0.9908 | 0.9946 | 0.9978 | 1.0 |
| UNMATCHED_PASS_CANDIDATE | 41 | 0 | 0.3985 | 0.5502 | 0.8775 | 0.9698 | 0.9979 | 0.9995 | 1.0011 |

## Categorical evidence

### pass_type

| Group | Value | Count | Proportion |
| --- | --- | ---: | ---: |
| TRUE_PASS_MATCH | same_team_pass | 20 | 66.67% |
| TRUE_PASS_MATCH | turnover_or_interception | 10 | 33.33% |
| ALL_FALSE_POSITIVES | same_team_pass | 29 | 43.28% |
| ALL_FALSE_POSITIVES | turnover_or_interception | 38 | 56.72% |
| SHOT_AS_PASS | same_team_pass | 5 | 50.00% |
| SHOT_AS_PASS | turnover_or_interception | 5 | 50.00% |
| INTERVENTION_AS_PASS | same_team_pass | 3 | 37.50% |
| INTERVENTION_AS_PASS | turnover_or_interception | 5 | 62.50% |
| CONTROL_AS_PASS | turnover_or_interception | 2 | 100.00% |
| CONTEST_AS_PASS | turnover_or_interception | 2 | 100.00% |
| AMBIGUOUS_ACTION_AS_PASS | same_team_pass | 1 | 25.00% |
| AMBIGUOUS_ACTION_AS_PASS | turnover_or_interception | 3 | 75.00% |
| UNMATCHED_PASS_CANDIDATE | same_team_pass | 20 | 48.78% |
| UNMATCHED_PASS_CANDIDATE | turnover_or_interception | 21 | 51.22% |

### auto_review_status

| Group | Value | Count | Proportion |
| --- | --- | ---: | ---: |
| TRUE_PASS_MATCH | strong_candidate | 9 | 30.00% |
| TRUE_PASS_MATCH | uncertain | 21 | 70.00% |
| ALL_FALSE_POSITIVES | strong_candidate | 15 | 22.39% |
| ALL_FALSE_POSITIVES | uncertain | 52 | 77.61% |
| SHOT_AS_PASS | strong_candidate | 1 | 10.00% |
| SHOT_AS_PASS | uncertain | 9 | 90.00% |
| INTERVENTION_AS_PASS | strong_candidate | 1 | 12.50% |
| INTERVENTION_AS_PASS | uncertain | 7 | 87.50% |
| CONTROL_AS_PASS | strong_candidate | 1 | 50.00% |
| CONTROL_AS_PASS | uncertain | 1 | 50.00% |
| CONTEST_AS_PASS | uncertain | 2 | 100.00% |
| AMBIGUOUS_ACTION_AS_PASS | strong_candidate | 1 | 25.00% |
| AMBIGUOUS_ACTION_AS_PASS | uncertain | 3 | 75.00% |
| UNMATCHED_PASS_CANDIDATE | strong_candidate | 11 | 26.83% |
| UNMATCHED_PASS_CANDIDATE | uncertain | 30 | 73.17% |

### automatic_outcome

| Group | Value | Count | Proportion |
| --- | --- | ---: | ---: |
| TRUE_PASS_MATCH | completed_pass | 20 | 66.67% |
| TRUE_PASS_MATCH | failed_pass | 10 | 33.33% |
| ALL_FALSE_POSITIVES | completed_pass | 29 | 43.28% |
| ALL_FALSE_POSITIVES | failed_pass | 38 | 56.72% |
| SHOT_AS_PASS | completed_pass | 5 | 50.00% |
| SHOT_AS_PASS | failed_pass | 5 | 50.00% |
| INTERVENTION_AS_PASS | completed_pass | 3 | 37.50% |
| INTERVENTION_AS_PASS | failed_pass | 5 | 62.50% |
| CONTROL_AS_PASS | failed_pass | 2 | 100.00% |
| CONTEST_AS_PASS | failed_pass | 2 | 100.00% |
| AMBIGUOUS_ACTION_AS_PASS | completed_pass | 1 | 25.00% |
| AMBIGUOUS_ACTION_AS_PASS | failed_pass | 3 | 75.00% |
| UNMATCHED_PASS_CANDIDATE | completed_pass | 20 | 48.78% |
| UNMATCHED_PASS_CANDIDATE | failed_pass | 21 | 51.22% |

### trajectory_status_counts

| Group | Value | Count | Proportion |
| --- | --- | ---: | ---: |
| TRUE_PASS_MATCH | contested | 108 | 10.09% |
| TRUE_PASS_MATCH | controlled | 66 | 6.17% |
| TRUE_PASS_MATCH | free | 815 | 76.17% |
| TRUE_PASS_MATCH | unknown | 81 | 7.57% |
| ALL_FALSE_POSITIVES | contested | 125 | 6.36% |
| ALL_FALSE_POSITIVES | controlled | 139 | 7.07% |
| ALL_FALSE_POSITIVES | free | 1303 | 66.28% |
| ALL_FALSE_POSITIVES | unknown | 399 | 20.30% |
| SHOT_AS_PASS | contested | 44 | 12.64% |
| SHOT_AS_PASS | controlled | 22 | 6.32% |
| SHOT_AS_PASS | free | 103 | 29.60% |
| SHOT_AS_PASS | unknown | 179 | 51.44% |
| INTERVENTION_AS_PASS | contested | 9 | 4.50% |
| INTERVENTION_AS_PASS | controlled | 17 | 8.50% |
| INTERVENTION_AS_PASS | free | 170 | 85.00% |
| INTERVENTION_AS_PASS | unknown | 4 | 2.00% |
| CONTROL_AS_PASS | contested | 2 | 2.56% |
| CONTROL_AS_PASS | controlled | 4 | 5.13% |
| CONTROL_AS_PASS | free | 72 | 92.31% |
| CONTEST_AS_PASS | contested | 6 | 7.89% |
| CONTEST_AS_PASS | controlled | 4 | 5.26% |
| CONTEST_AS_PASS | free | 66 | 86.84% |
| AMBIGUOUS_ACTION_AS_PASS | contested | 18 | 14.75% |
| AMBIGUOUS_ACTION_AS_PASS | controlled | 10 | 8.20% |
| AMBIGUOUS_ACTION_AS_PASS | free | 49 | 40.16% |
| AMBIGUOUS_ACTION_AS_PASS | unknown | 45 | 36.89% |
| UNMATCHED_PASS_CANDIDATE | contested | 46 | 4.03% |
| UNMATCHED_PASS_CANDIDATE | controlled | 82 | 7.18% |
| UNMATCHED_PASS_CANDIDATE | free | 843 | 73.82% |
| UNMATCHED_PASS_CANDIDATE | unknown | 171 | 14.97% |


## Missed gold passes — root causes

- `W1-E005` @ 21.0s · **NO_SOURCE_CONTACT** · contacts `[]` · pairs `[]` · rejections `{}`
- `W1-E013` @ 42.0s · **SAME_PLAYER_SKIP** · contacts `['event-0030', 'event-0031']` · pairs `[]` · rejections `{}`
- `W2-E019` @ 615.0s · **NO_SOURCE_CONTACT** · contacts `[]` · pairs `[]` · rejections `{}`
- `W3-E002` @ 1273.3s · **SAME_PLAYER_SKIP** · contacts `['event-0051']` · pairs `[]` · rejections `{}`
- `W3-E014` @ 1323.3s · **SAME_PLAYER_SKIP** · contacts `['event-0072', 'event-0073', 'event-0074']` · pairs `[]` · rejections `{}`
- `W3-E017` @ 1331.3s · **SAME_PLAYER_SKIP** · contacts `['event-0081', 'event-0082']` · pairs `[]` · rejections `{}`
- `W4-E007` @ 1357.0s · **SAME_PLAYER_SKIP** · contacts `['event-0110', 'event-0111']` · pairs `[]` · rejections `{}`
- `W4-E008` @ 1358.0s · **EXCLUDED_BY_RELEASE_POLICY** · contacts `['event-0111', 'event-0112', 'event-0113', 'event-0114', 'event-0115', 'event-0116']` · pairs `['pass-0062', 'pass-0063', 'pass-0064']` · rejections `{'pass-0062': ['ball_displacement_too_short', 'ball_path_too_short', 'ball_never_left_source_player', 'immediate_contested_tackle'], 'pass-0063': ['release_too_short', 'ball_displacement_too_short', 'ball_path_too_short', 'immediate_contested_tackle']}`
- `W4-E017` @ 1413.0s · **SAME_PLAYER_SKIP** · contacts `['event-0137']` · pairs `[]` · rejections `{}`
- `W4-E018` @ 1415.0s · **NO_SOURCE_CONTACT** · contacts `[]` · pairs `[]` · rejections `{}`
- `W5-E006` @ 1817.0s · **SOURCE_CONTACT_PRESENT_TARGET_MISSING** · contacts `['event-0022']` · pairs `[]` · rejections `{}`
- `W6-E001` @ 1913.0s · **NO_SOURCE_CONTACT** · contacts `[]` · pairs `[]` · rejections `{}`
- `W6-E010` @ 1950.0s · **SAME_PLAYER_SKIP** · contacts `['event-0063']` · pairs `[]` · rejections `{}`
- `W6-E011` @ 1954.0s · **SAME_PLAYER_SKIP** · contacts `['event-0064', 'event-0065', 'event-0066']` · pairs `[]` · rejections `{}`
- `W6-E012` @ 1955.0s · **SAME_PLAYER_SKIP** · contacts `['event-0065', 'event-0066', 'event-0067']` · pairs `[]` · rejections `{}`
- `W6-E016` @ 1962.0s · **EXCLUDED_BY_RELEASE_POLICY** · contacts `['event-0072', 'event-0073', 'event-0074']` · pairs `['pass-0044']` · rejections `{'pass-0044': ['ball_displacement_too_short']}`
- `W6-E017` @ 1963.0s · **EXCLUDED_BY_RELEASE_POLICY** · contacts `['event-0072', 'event-0073', 'event-0074']` · pairs `['pass-0044']` · rejections `{'pass-0044': ['ball_displacement_too_short']}`
- `W6-E019` @ 1986.5s · **NO_SOURCE_CONTACT** · contacts `[]` · pairs `[]` · rejections `{}`

## False-positive root causes

- `AMBIGUOUS_ACTION_AS_PASS`: **4**
- `CONTEST_AS_PASS`: **2**
- `CONTINUED_CONTROL_EVIDENCE`: **23**
- `CONTROL_AS_PASS`: **2**
- `INTERVENTION_AS_PASS`: **8**
- `REPEATED_CANDIDATE_CLUSTER`: **18**
- `SHOT_AS_PASS`: **10**

## False-positive clusters

- Diagnostic clustering gap: **1.0s** within one physical source match.
- False-positive candidates / clusters: **67 / 43**
- Cluster size distribution: `{1: 32, 2: 3, 3: 5, 4: 2, 6: 1}`
- `fp-cluster-001` · 5e62625e · 1785.137–1787.339s · size 6 · `['pass-0002', 'pass-0003', 'pass-0004', 'pass-0005', 'pass-0007', 'pass-0008']`
- `fp-cluster-005` · 5e62625e · 1819.642–1820.676s · size 4 · `['pass-0014', 'pass-0015', 'pass-0016', 'pass-0017']`
- `fp-cluster-007` · 5e62625e · 1831.955–1832.99s · size 3 · `['pass-0023', 'pass-0024', 'pass-0025']`
- `fp-cluster-012` · 5e62625e · 1964.501–1964.734s · size 3 · `['pass-0045', 'pass-0046', 'pass-0047']`
- `fp-cluster-013` · 5e62625e · 1967.571–1968.172s · size 2 · `['pass-0048', 'pass-0049']`
- `fp-cluster-016` · 6d8fc20c · 1314.124–1314.625s · size 2 · `['pass-0031', 'pass-0032']`
- `fp-cluster-020` · 6d8fc20c · 1335.679–1336.28s · size 3 · `['pass-0040', 'pass-0041', 'pass-0042']`
- `fp-cluster-034` · 9c7485e4 · 45.512–47.648s · size 4 · `['pass-0023', 'pass-0024', 'pass-0025', 'pass-0026']`
- `fp-cluster-035` · 9c7485e4 · 63.163–63.63s · size 2 · `['pass-0030', 'pass-0031']`
- `fp-cluster-038` · 9c7485e4 · 563.33–564.131s · size 3 · `['pass-0177', 'pass-0178', 'pass-0179']`
- `fp-cluster-041` · 9c7485e4 · 593.26–594.595s · size 3 · `['pass-0187', 'pass-0188', 'pass-0189']`

## Oracle diagnostics — impossible / evaluation-only

### A_PERFECT_FALSE_POSITIVE_REMOVAL

- Keep only currently matched true candidates; recall remains limited by missed gold passes.
- Attempts: **30** · Corgi share: **56.67%** · completion: **66.67%**

### B_PERFECT_OUTCOME_ON_MATCHED_EVENTS

- Keep all candidates; substitute correct broad outcome only on matched events.
- Attempts: **97** · Corgi share: **50.52%** · completion: **50.52%**

### C_PERFECT_TEAM_ATTRIBUTION_ON_MATCHED_EVENTS

- Keep all candidates; substitute gold actor team only on matched events.
- Attempts: **97** · Corgi share: **44.33%** · completion: **50.52%**

### D_PERFECT_MATCHED_EVENTS_KEEP_FALSE_POSITIVES

- Correct team and broad outcome only for matched events while retaining all false positives.
- Attempts: **97** · Corgi share: **44.33%** · completion: **50.52%**

## Candidate v3 hypotheses

### Collapse repeated candidate pairs around one short contact cluster

- Physical interpretation: Several generated source→target pairs can describe one rebound, shot or fragmented contact action.
- Runtime evidence: Existing pass candidate timestamps and consecutive-contact identifiers only.
- Supporting windows: `['W1', 'W2', 'W3', 'W5', 'W6']`; contradicting windows: `['W4']`
- Genuine pass types at risk: Fast one-touch passing sequences can also be dense; preserve distinct player-to-player releases.
- Measured support: 35 false-positive candidates occur in multi-candidate diagnostic clusters; true clustered count not inferred as football truth (0).

### Trace same-player skip chains to a later distinct receiver before discarding a release

- Physical interpretation: The consecutive-contact builder can observe several contacts under one stable identity while a gold pass is anchored in the same sequence.
- Runtime evidence: Existing ordered contact IDs, stable-player IDs, skipped-pair reason and release trajectory evidence.
- Supporting windows: `['W1', 'W3', 'W4', 'W6']`; contradicting windows: `['W2', 'W5']`
- Genuine pass types at risk: Most same-player chains are continued control; any shadow rule must require a later distinct receiver and visible free-flight evidence.
- Measured support: 9 of 18 missed gold passes trace to a recorded same_player_consecutive_contacts skip.

No hypothesis in this report is implemented or promoted. Production remains Pass Policy v1.
