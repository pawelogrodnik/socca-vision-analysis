import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React from 'react';

import { KeyMomentsEditorModal } from '../src/components/KeyMomentsEditorModal.tsx';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });
const { cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');
afterEach(() => cleanup());

const report = { id: 'published-one', teams: [{ team_id: 'corgi', team_name: 'Corgi' }, { team_id: 'verisk', team_name: 'Verisk' }], players: [] } as never;
const suggestion = {
  status: 'ready', candidate_generation_digest: 'lineage-a', candidate_count: 1, unreviewed_count: 1,
  candidates: [{ candidate_id: 'iac-verisk', start_time_sec: 100, peak_time_sec: 105, end_time_sec: 112, team_id: 'verisk', interestingness_score: .8, confidence: .7, evidence: [{ kind: 'progressive_pass_sequence' }, { kind: 'regain' }] }],
  overlaps: { 'iac-verisk': { kind: 'extension', overlap_sec: 5, moment_id: 'one', headline: 'Istniejący', start_time_sec: 102, end_time_sec: 108 } },
} as const;

function state(overrides = {}) {
  return { key_moment_editor_allowed: true, revision: 'r1', moments: [
    { moment_id: 'first', time_sec: 40, category: 'other', headline: 'Pierwszy', origin: 'manual' },
    { moment_id: 'second', time_sec: 80, category: 'other', headline: 'Drugi', origin: 'manual' },
  ], suggestions: suggestion, ...overrides };
}

test('timestamp edits and manual additions keep the current curated card order stable', () => {
  const view = render(React.createElement(KeyMomentsEditorModal, { state: state(), report, onClose: () => undefined, onSave: async () => undefined }));
  const times = view.getAllByLabelText('Czas') as HTMLInputElement[];
  fireEvent.input(times[0], { target: { value: '2:30' } }); fireEvent.blur(times[0]);
  fireEvent.click(view.getByRole('button', { name: 'Dodaj moment' }));
  const rows = [...view.container.querySelectorAll('.key-moment-editor-row strong')].map((row) => row.textContent);
  assert.deepEqual(rows.map((row) => row?.split('.')[0]), ['0:00', '2:30', '1:20']);
});

test('suggestion is separate, prefilled with its team, warns about overlap, and appends on accept', async () => {
  let accepted: unknown;
  const acceptedMoment = { moment_id: 'manual-km-new', time_sec: 105, category: 'other', headline: 'Moment do weryfikacji', team_id: 'verisk', origin: 'manual' };
  const view = render(React.createElement(KeyMomentsEditorModal, {
    state: state(), report, onClose: () => undefined, onSave: async () => undefined,
    onAcceptSuggestion: async (payload) => { accepted = payload; return { ...state({ revision: 'r2', moments: [...state().moments, acceptedMoment], suggestions: { ...suggestion, candidates: [], unreviewed_count: 0 } }), accepted_moment: acceptedMoment }; },
  }));
  assert.ok(view.getByText('Sugerowane Key Moments (1)'));
  assert.ok(view.getByText(/progressive_pass_sequence/));
  assert.ok(view.getByText(/Możliwe rozszerzenie/));
  const teamSelectors = view.getAllByLabelText('Drużyna') as HTMLSelectElement[];
  assert.equal(teamSelectors.at(-1)?.value, 'verisk');
  fireEvent.click(view.getByRole('button', { name: 'Akceptuj jako Key Moment' }));
  await waitFor(() => assert.ok(accepted));
  await waitFor(() => assert.equal(view.container.querySelectorAll('.key-moment-editor-row strong').length, 3));
  const rows = [...view.container.querySelectorAll('.key-moment-editor-row strong')].map((row) => row.textContent?.split('.')[0]);
  assert.deepEqual(rows, ['1:45', '0:40', '1:20']);
  assert.ok(view.getByText('Sugerowane Key Moments (0)'));
});

test('reject persists through callback and removes only that suggested candidate', async () => {
  let rejected: unknown;
  const view = render(React.createElement(KeyMomentsEditorModal, {
    state: state(), report, onClose: () => undefined, onSave: async () => undefined,
    onRejectSuggestion: async (payload) => { rejected = payload; return state({ suggestions: { ...suggestion, candidates: [], unreviewed_count: 0 } }); },
  }));
  fireEvent.click(view.getByRole('button', { name: 'Odrzuć' }));
  await waitFor(() => assert.deepEqual(rejected, { candidate_id: 'iac-verisk', candidate_generation_digest: 'lineage-a' }));
  assert.ok(view.getByText('Brak nieprzejrzanych sugestii.'));
});
