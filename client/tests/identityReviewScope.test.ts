import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { resolve } from 'node:path';

import {
  playerStatsChoiceFromScope,
  scopeForPlayerStatsChoice,
} from '../src/utils/identityReviewScope.ts';


test('Corgi/Team A player stats maps opponent to team stats only', () => {
  assert.deepEqual(scopeForPlayerStatsChoice('A').teams, {
    A: 'complete_roster',
    B: 'team_stats_only',
  });
});

test('both teams maps both teams to complete roster', () => {
  assert.deepEqual(scopeForPlayerStatsChoice('both').teams, {
    A: 'complete_roster',
    B: 'complete_roster',
  });
});

test('legacy absent scope is presented as both without mutating metadata', () => {
  assert.equal(playerStatsChoiceFromScope(undefined), 'both');
});

test('scope selector renders actual team names and hides persisted policy identifiers', () => {
  const source = readFileSync(
    resolve(import.meta.dirname, '../src/components/IdentityReviewScopeSelector.tsx'),
    'utf8',
  );
  assert.match(source, /teams\[0\]\?\.name/);
  assert.match(source, /teams\[1\]\?\.name/);
  assert.match(source, /Statystyki zawodników/);
  assert.match(source, /Obie drużyny/);
  assert.doesNotMatch(source, />complete_roster</);
  assert.doesNotMatch(source, />team_stats_only</);
});

test('Remaining Cases UI separates required work from optional Team-A MAX audit', () => {
  const source = readFileSync(
    resolve(import.meta.dirname, '../src/components/IdentityExceptionReviewPanel.tsx'),
    'utf8',
  );
  const correctionForm = readFileSync(
    resolve(import.meta.dirname, '../src/components/ReviewedIdentityCorrectionForm.tsx'),
    'utf8',
  );
  const queueUtils = readFileSync(
    resolve(import.meta.dirname, '../src/utils/identityExceptionQueue.ts'),
    'utf8',
  );
  assert.match(source, /Wymagane/);
  assert.match(source, /Kontynuuj do MAX/);
  assert.match(source, /nie blokuje zakończenia Review/);
  assert.match(source, /transition\.synchronization === 'completion'/);
  assert.match(source, /transition\.synchronization === 'replenish'/);
  assert.match(queueUtils, /queue === 'required'/);
  assert.match(source, /loadCases\(undefined, false, 0, 0, 'all', nextQueue\)/);
  assert.match(source, /REQUIRED_REVIEW_WORKING_WINDOW_SIZE/);
  assert.match(source, /recordDurableRequiredReviewSave/);
  assert.doesNotMatch(source, /pageOffset \+ REVIEW_PAGE_SIZE/);
  assert.match(source, /Zapisz \+ następny/);
  assert.match(correctionForm, /Zawodnik z kadry/);
  assert.doesNotMatch(correctionForm, /Team A lub Team B/);
});

test('ready-to-finalize UI exposes optional Team-A MAX audit', () => {
  const source = readFileSync(
    resolve(import.meta.dirname, '../src/components/IdentityReviewWorkspace.tsx'),
    'utf8',
  );
  assert.match(source, /Kontynuuj do MAX/);
  assert.match(source, /initialQueue='optional_audit'/);
});

test('Reviewed Identity visibly summarizes player-level and team-only scope', () => {
  const source = readFileSync(
    resolve(import.meta.dirname, '../src/components/IdentityReviewScopeSummary.tsx'),
    'utf8',
  );
  assert.match(source, /Zakres Review/);
  assert.match(source, /tylko statystyki drużynowe/);
  assert.match(source, /statystyki zawodników/);
  assert.match(source, /teams\[index\]\?\.name/);
});
