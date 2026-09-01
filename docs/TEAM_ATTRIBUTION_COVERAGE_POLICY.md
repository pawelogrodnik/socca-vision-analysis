# Team-attribution coverage policy

Required Review is a safety gate, not an instruction to resolve every source
that happens to have usable crops. Ordinary Team-U and noisy A/B sources are
selected only when their unique exact observations are needed to reach the
global 90% team-known target. Structural ownership conflicts remain Required
regardless of coverage.

The short-track policy `short_track_dominant_team_v1` may derive a team only
for a source of at most 200 frames with at least 15 known observations, a
dominant ratio of at least 0.85, no structural/operator/stale conflict, and no
minority run longer than eight observations. Its generated, replaceable
provenance is stored in `reviewed_team_attribution_policy.json`.

Human decisions are append-only in `review_operator_decision_audit.json`.
`review_decision_benchmark.json` reports operator agreement with the dominant
upstream signal; this is deliberately not called system accuracy. Historical
backfill reads legacy stores without mutating decisions and marks provenance as
`EXACT_PERSISTED`, `RECONSTRUCTED`, or `UNAVAILABLE`.
