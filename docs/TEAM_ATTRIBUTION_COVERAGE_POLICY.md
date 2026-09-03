# Team-attribution coverage policy

Required Review is a safety gate, not an instruction to resolve every source
that happens to have usable crops. Ordinary Team-U and noisy A/B sources are
selected only when their unique exact observations are needed to reach the
global 90% team-known target. Structural ownership conflicts remain Required
regardless of coverage.

The short-track policy `short_track_dominant_team_v1` may derive a team only
for a source of at most 200 frames with at least 15 known observations, a
dominant ratio of at least 0.85, no structural/operator/stale conflict, and no
minority run longer than eight observations or more than six A/B switches.
Its generated, replaceable provenance is stored in both the authoritative
Reviewed snapshot and `reviewed_team_attribution_policy.json`. The snapshot
projection is exact-source scoped: effective observations, team coverage and
Reviewed stats therefore consume the same derived A/B truth as the queue.

Human decisions are append-only in `review_operator_decision_audit.json`.
`review_decision_benchmark.json` reports operator agreement with the dominant
upstream signal; this is deliberately not called system accuracy. Historical
backfill reads legacy stores without mutating decisions. It distinguishes the
persisted decision record from reconstructed team features and only reconstructs
features when exact owned frames plus the current source digest can be proven;
otherwise team features are `UNAVAILABLE`.
