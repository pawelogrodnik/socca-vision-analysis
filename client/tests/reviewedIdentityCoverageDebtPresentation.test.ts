import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

test('coverage debt presentation explains pending Mixed without changing workflow access', () => {
  const components = new URL('../src/components/', import.meta.url);
  const summary = readFileSync(new URL('ReviewedIdentityCoverageDebtSummary.tsx', components), 'utf8');

  assert.match(summary, /Gdzie jest pozostałe pokrycie/);
  assert.match(summary, /Brak zwykłych wymaganych przypadków/);
  assert.match(summary, /Mixed Players stanie się dostępne po zakończeniu wymaganych przypadków/);
  assert.match(summary, /do \$\{formatReviewedIdentityPercentagePoints/);
  assert.match(summary, /team\.scope === 'team_stats_only'/);
  assert.match(summary, /Dodatkowo \{debt\.ambiguous\.mixed_case_count\} przypadki Mixed/);
  assert.doesNotMatch(summary, /onClick|Open Mixed|Otwórz Mixed/);
});

test('review panel consumes authoritative backend debt instead of recomputing buckets', () => {
  const components = new URL('../src/components/', import.meta.url);
  const panel = readFileSync(new URL('IdentityExceptionReviewPanel.tsx', components), 'utf8');

  assert.match(panel, /setCoverageDebt\(progress\.coverage_debt \|\| null\)/);
  assert.match(panel, /<ReviewedIdentityCoverageDebtSummary/);
  assert.doesNotMatch(panel, /remaining_actionable_named_gain.*coverageDebt/);
});
