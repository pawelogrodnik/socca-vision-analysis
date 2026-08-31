# Per-team Reviewed Identity scope

Reviewed Identity stores an explicit policy for each team under
`match.json.identity_review_scope.teams`:

```json
{
  "identity_review_scope": {
    "schema_version": "1.0.0",
    "teams": {
      "A": "complete_roster",
      "B": "team_stats_only"
    }
  }
}
```

`complete_roster` requires the existing named-player coverage and safety gates.
`team_stats_only` does **not** ignore the team. Team attribution, Team U,
cross-team contradictions, semantic conflicts and Mixed Players remain part of
the required safety workflow. Only missing opponent names become informational.

Clean anonymous observations whose team is known still contribute to team
analytics. Reviewable subjects from a `team_stats_only` team are available in a
separate, paginated optional audit. That queue never contributes to blocking
workload or finalization readiness, but it retains all correction actions,
including assignment to a player from the other team.

Public reviewed reports omit individual player rows for a `team_stats_only`
team and expose `player_stats_status: not_reviewed_by_scope`. Team movement
continues to include safely team-attributed reviewed observations, including
unnamed and same-team player-conflicted observations. Team U, live A/B
attribution conflicts, non-player labels, untrusted views, outside-play
observations and invalid pitch positions remain excluded. Matches and reports
without explicit scope preserve the previous coverage policy.

Reviewed team movement currently supplies safe distance and high-intensity
distance. Team sprint counts remain on the existing legacy team-statistics
authority until one shared sprint classifier is available for both player and
team reporting.

Changing scope preserves all operator decisions. It changes the semantic scope
digest, invalidates `reviewed_identity_progress.json`, and rebuilds only the
identity policy/readiness view. It does not rerun detection, tracking, render or
statistics; those remain part of the normal finalization path.
