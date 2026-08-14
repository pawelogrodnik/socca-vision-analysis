import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import type { ReviewedIdentityReviewFilters, Team } from '../src/types.ts';
import {
  apiTeamFilter,
  matchTeamName,
  teamReviewFilterOptions,
} from '../src/utils/identityExceptionTeamFilter.ts';


const teams: Team[] = [
  { id: 'red', name: 'Red FC', players: [] },
  { id: 'blue', name: 'Blue FC', players: [] },
];

const filters: ReviewedIdentityReviewFilters = {
  active_team_label: null,
  counts: { all: 531, A: 147, B: 302, U: 82 },
};


test('team review options use real match names and canonical full-queue counts', () => {
  assert.deepEqual(teamReviewFilterOptions(teams, filters), [
    { value: 'all', label: 'Wszystkie', count: 531 },
    { value: 'A', label: 'Red FC', count: 147 },
    { value: 'B', label: 'Blue FC', count: 302 },
  ]);
  assert.equal(filters.counts.A + filters.counts.B + filters.counts.U, filters.counts.all);
});


test('missing match names fall back safely to Team A and Team B', () => {
  assert.equal(matchTeamName([], 'A'), 'Team A');
  assert.equal(matchTeamName([], 'B'), 'Team B');
});


test('All omits the API filter while team tabs send A or B', () => {
  assert.equal(apiTeamFilter('all'), undefined);
  assert.equal(apiTeamFilter('A'), 'A');
  assert.equal(apiTeamFilter('B'), 'B');
});


test('exception panel resets filters to page one and preserves filters for navigation and save', () => {
  const panel = readFileSync(
    resolve(import.meta.dirname, '../src/components/IdentityExceptionReviewPanel.tsx'),
    'utf8',
  );
  const api = readFileSync(resolve(import.meta.dirname, '../src/api.ts'), 'utf8');

  assert.match(panel, /changeTeamFilter\(nextFilter: TeamReviewFilter\)/);
  assert.match(panel, /setPageOffset\(0\)/);
  assert.match(panel, /loadCases\(undefined, false, 0, 0, nextFilter, activeQueue\)/);
  assert.match(panel, /destination\.index,[\s\S]*activeTeamFilter/);
  assert.match(panel, /pageOffset \+ REVIEW_PAGE_SIZE,[\s\S]*activeTeamFilter/);
  assert.match(panel, /finalizeCorrections\(activeTeamFilter, activeQueue\)/);
  assert.match(panel, /Brak pozostałych przypadków dla \{activeTeamName\}/);
  assert.match(panel, /Łącznie pozostało: \{globalRemaining\}/);
  assert.match(panel, /className=\{activeTeamFilter === team \? 'active-team' : ''\}/);
  assert.match(api, /query\.set\('team_label', teamLabel\)/);
});
