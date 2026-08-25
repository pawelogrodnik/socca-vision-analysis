import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import type { ReviewedIdentityCoverageDebt } from '../src/types.ts';
import { coverageDebtPresentationTeams, requiredBreakdownLabel } from '../src/utils/reviewedIdentityCoverageDebtPresentation.ts';

function debt(scopeB: 'complete_roster' | 'team_stats_only' = 'complete_roster'): ReviewedIdentityCoverageDebt {
  const bucket = (observations = 0) => ({ case_count: 0, unique_observations: observations, share_of_reliable: observations / 100, coverage_pp: observations });
  const team = (scope: 'complete_roster' | 'team_stats_only') => ({
    scope,
    reliable_observations: 100,
    current_named_observations: 20,
    current_named_coverage: 0.2,
    target_named_coverage: scope === 'complete_roster' ? 0.9 : null,
    target_named_observations: scope === 'complete_roster' ? 90 : null,
    target_gap_observations: scope === 'complete_roster' ? 70 : null,
    target_gap_pp: scope === 'complete_roster' ? 70 : null,
    projected_named_coverage_after_committed: 0.2,
    unnamed_observations: 80,
    operator_identity_debt_observations: scope === 'complete_roster' ? 80 : 0,
    not_required_by_scope: bucket(scope === 'team_stats_only' ? 80 : 0),
    accounted_unnamed_observations: 80,
    unaccounted_unnamed_observations: 0,
    buckets: {
      committed_pending: bucket(), required: bucket(), mixed: bucket(), optional_max: bucket(), unavailable: bucket(scope === 'complete_roster' ? 80 : 0),
    },
  });
  return {
    policy_version: 'test', coverage_unit: 'pair', accounting_precedence: [],
    per_team: { A: team('complete_roster'), B: team(scopeB) },
    ambiguous: { mixed_case_count: 0, raw_marker_observations: 0, note: '' },
  };
}

test('team stats only presentation has scope information without identity debt', () => {
  const [a, b] = coverageDebtPresentationTeams(debt('team_stats_only'));
  assert.equal(a.show, true);
  assert.equal(b.isTeamStatsOnly, true);
  assert.equal(b.show, true);
  assert.equal(b.team.buckets.unavailable.unique_observations, 0);
  assert.equal(b.team.not_required_by_scope.unique_observations, 80);
});

test('required breakdown uses product labels', () => {
  assert.equal(requiredBreakdownLabel('semantic'), 'Drużyna / konflikt');
  assert.equal(requiredBreakdownLabel('continuity'), 'Ciągłość');
  assert.equal(requiredBreakdownLabel('coverage'), 'Pokrycie imienne');
});

test('coverage debt presentation explains pending Mixed without changing workflow access', () => {
  const components = new URL('../src/components/', import.meta.url);
  const summary = readFileSync(new URL('ReviewedIdentityCoverageDebtSummary.tsx', components), 'utf8');

  assert.match(summary, /Gdzie jest pozostałe pokrycie/);
  assert.match(summary, /Brak zwykłych wymaganych przypadków/);
  assert.match(summary, /Mixed Players stanie się dostępne po zakończeniu wymaganych przypadków/);
  assert.match(summary, /do \$\{formatReviewedIdentityPercentagePoints/);
  assert.match(summary, /Rozpoznanie zawodników tej drużyny nie jest wymagane/);
  assert.match(summary, /Dodatkowo \{debt\.ambiguous\.mixed_case_count\} przypadki Mixed/);
  assert.doesNotMatch(summary, /onClick|Open Mixed|Otwórz Mixed/);
});

test('review panel consumes authoritative backend debt instead of recomputing buckets', () => {
  const components = new URL('../src/components/', import.meta.url);
  const panel = readFileSync(new URL('IdentityExceptionReviewPanel.tsx', components), 'utf8');

  assert.match(panel, /setCoverageDebt\(progress\.coverage_debt \|\| null\)/);
  assert.match(panel, /result\.coverage_debt\) setCoverageDebt\(result\.coverage_debt\)/);
  assert.match(panel, /<ReviewedIdentityCoverageDebtSummary/);
  assert.doesNotMatch(panel, /remaining_actionable_named_gain.*coverageDebt/);
});
