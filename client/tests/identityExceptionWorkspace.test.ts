import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  moveReviewCaseIndex,
  persistReviewDecision,
} from '../src/utils/identityExceptionWorkspace.ts';


test('previous and next navigation preserve queue boundaries', () => {
  assert.equal(moveReviewCaseIndex(1, 4), 1);
  assert.equal(moveReviewCaseIndex(3, 4), 3);
  assert.equal(moveReviewCaseIndex(-1, 4), 0);
  assert.equal(moveReviewCaseIndex(4, 4), 3);
  assert.equal(moveReviewCaseIndex(1, 0), 0);
});


test('successful save persists exactly once before advancing the queue', async () => {
  let persistenceCalls = 0;
  let savedCalls = 0;
  const events: string[] = [];

  const result = await persistReviewDecision(
    async () => {
      persistenceCalls += 1;
      events.push('persisted');
      return { id: 'saved-case' };
    },
    (saved) => {
      savedCalls += 1;
      events.push(`advanced:${saved.id}`);
    },
  );

  assert.deepEqual(result, { id: 'saved-case' });
  assert.equal(persistenceCalls, 1);
  assert.equal(savedCalls, 1);
  assert.deepEqual(events, ['persisted', 'advanced:saved-case']);
});


test('failed save never advances the queue', async () => {
  let persistenceCalls = 0;
  let savedCalls = 0;

  await assert.rejects(
    persistReviewDecision(
      async () => {
        persistenceCalls += 1;
        throw new Error('save failed');
      },
      () => { savedCalls += 1; },
    ),
    /save failed/,
  );

  assert.equal(persistenceCalls, 1);
  assert.equal(savedCalls, 0);
});


test('exception workstation keeps one active case and stateful correction subviews', () => {
  const components = new URL('../src/components/', import.meta.url);
  const panel = readFileSync(new URL('IdentityExceptionReviewPanel.tsx', components), 'utf8');
  const form = readFileSync(new URL('ReviewedIdentityCorrectionForm.tsx', components), 'utf8');

  assert.match(panel, /Przypadek \{index \+ 1\} z \{cases\.length\}/);
  assert.match(panel, /identity-exception-workstation/);
  assert.match(panel, /key=\{reviewUnitKey\(reviewCase\.unit\)\}/);
  assert.match(panel, /onPrevious: \(\) => moveToCase\(index - 1\)/);
  assert.match(panel, /onNext: \(\) => moveToCase\(index \+ 1\)/);
  assert.match(form, /returnToCategories/);
  assert.match(form, /← Wróć/);
  assert.match(form, /showActionCategories && segmentScope/);
  assert.match(form, /action === 'assign_roster_player'/);
  assert.match(form, /Zapisz \+ następny|navigation\.saveLabel/);
  assert.match(form, /persistReviewDecision/);
});
