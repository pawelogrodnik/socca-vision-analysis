import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import { JSDOM } from 'jsdom';
import React from 'react';

import { MixedPlayersReviewPanel, type MixedPlayersReviewApi } from '../src/components/MixedPlayersReviewPanel.tsx';
import type { Match, MixedPlayerCase, MixedPlayerFocusedCaseResponse, MixedPlayersReviewQueue, ReviewWorkflow } from '../src/types.ts';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { act, cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');

afterEach(() => {
  cleanup();
  dom.window.confirm = () => true;
});

const match = {
  id: 'match-1',
  title: 'Corgi – Verisk',
  teams: [
    { id: 'team-a', name: 'Corgi', players: [] },
    { id: 'team-b', name: 'Verisk', players: [] },
  ],
} as unknown as Match;

const workflow = {
  schema_version: '1.0.0',
  match_id: match.id,
  available: true,
  phase: 'review',
  status: 'action_required',
  current_step_id: 'mixed_players',
  review_complete: false,
  can_enter_report: false,
  can_publish: false,
  steps: [],
  required_action: { type: 'review_mixed_players', step_id: 'mixed_players' },
  issues: { blocking: 3, normal_blocking: 0, mixed_blocking: 3, important: 0, semantic: 0, coverage: 0, optional: 0 },
  freshness: {
    reviewed_identity_current: false,
    reviewed_stats_current: false,
    reviewed_output_current: false,
    qa_approval_current: false,
  },
  blockers: [],
  allowed_actions: ['review_mixed_players'],
} satisfies ReviewWorkflow;

function mixedCase(caseId: string, observationCount: number): MixedPlayerCase {
  return {
    case_id: caseId,
    candidate_subject_id: `subject-${caseId}`,
    original_issue: 'mixed_players',
    mixed_hint: 'cross_team',
    resolution_status: 'unresolved',
    source_subject_digest: `digest-${caseId}`,
    source_tracklet_ids: [`track-${caseId}`],
    observation_count: observationCount,
    frame_start: observationCount,
    frame_end: observationCount + 10,
    blocking: true,
    scope_status: 'blocking',
    temporal_evidence: {
      status: 'ready',
      anchor_crops: [{
        anchor_crop_id: `crop-${caseId}`,
        artifact: `${caseId}.jpg`,
        frame: observationCount,
        time_sec: observationCount,
      }],
    },
  };
}

function queue(cases: MixedPlayerCase[]): MixedPlayersReviewQueue {
  return {
    schema_version: '1.0.0',
    mode: 'blocking_only',
    match_id: match.id,
    summary: {
      total: cases.length,
      unresolved: cases.length,
      unresolved_total: cases.length,
      nonblocking_by_scope: 0,
      resolved: 0,
      complex_unresolved: 0,
    },
    assignment_options: { roster: [], slots: [] },
    cases,
  };
}

function focusedResponse(
  caseId: string,
  status: MixedPlayerFocusedCaseResponse['status'],
  reviewCase: MixedPlayerCase | null,
): MixedPlayerFocusedCaseResponse {
  return {
    schema_version: '1.0.0',
    mode: 'reviewed_identity_mixed_focused_case',
    match_id: match.id,
    requested_case_id: caseId,
    status,
    case: reviewCase,
    assignment_options: { roster: [], slots: [] },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

function renderPanel(reviewApi: Partial<MixedPlayersReviewApi>) {
  return render(React.createElement(MixedPlayersReviewPanel, {
    match,
    workflow,
    onWorkflowChanged: () => undefined,
    reviewApi,
  }));
}

test('M1 remains visible until the exact focused M2 evidence is ready', async () => {
  const m1 = mixedCase('M1', 11);
  const m2 = mixedCase('M2', 22);
  const pendingM2 = deferred<MixedPlayerFocusedCaseResponse>();
  const focusedIds: string[] = [];
  let fullQueueReads = 0;
  let reprojects = 0;
  const view = renderPanel({
    getQueue: async () => { fullQueueReads += 1; return queue([m1, m2]); },
    getFocusedCase: async (_matchId, caseId) => {
      focusedIds.push(caseId);
      return pendingM2.promise;
    },
    reprojectWorkflow: async () => { reprojects += 1; return workflow; },
  });

  await waitFor(() => assert.ok(view.getByText('11 wykrytych obserwacji')));
  fireEvent.click(view.getByRole('button', { name: 'Następny' }));

  await waitFor(() => assert.deepEqual(focusedIds, ['M2']));
  assert.ok(view.getByText('11 wykrytych obserwacji'));
  assert.equal(view.queryByText('22 wykrytych obserwacji'), null);

  await act(async () => pendingM2.resolve(focusedResponse('M2', 'current_blocking', m2)));
  await waitFor(() => assert.ok(view.getByText('22 wykrytych obserwacji')));
  assert.equal(fullQueueReads, 1);
  assert.equal(reprojects, 0);
});

test('manual drift reconciles the full queue once and exact-focuses logical M3', async () => {
  const m1 = mixedCase('M1', 11);
  const m2 = mixedCase('M2', 22);
  const m3 = mixedCase('M3', 33);
  const pendingM3 = deferred<MixedPlayerFocusedCaseResponse>();
  const focusedIds: string[] = [];
  let fullQueueReads = 0;
  let reprojects = 0;
  const view = renderPanel({
    getQueue: async () => {
      fullQueueReads += 1;
      return fullQueueReads === 1 ? queue([m1, m2, m3]) : queue([m1, m3]);
    },
    getFocusedCase: async (_matchId, caseId) => {
      focusedIds.push(caseId);
      if (caseId === 'M2') return focusedResponse('M2', 'no_longer_unresolved', null);
      if (caseId === 'M3') return pendingM3.promise;
      throw new Error(`unexpected focus ${caseId}`);
    },
    reprojectWorkflow: async () => { reprojects += 1; return workflow; },
  });

  await waitFor(() => assert.ok(view.getByText('11 wykrytych obserwacji')));
  fireEvent.click(view.getByRole('button', { name: 'Następny' }));

  await waitFor(() => assert.deepEqual(focusedIds, ['M2', 'M3']));
  assert.ok(view.getByText('11 wykrytych obserwacji'));
  assert.equal(view.queryByText('33 wykrytych obserwacji'), null);
  await act(async () => pendingM3.resolve(focusedResponse('M3', 'current_blocking', m3)));
  await waitFor(() => assert.ok(view.getByText('33 wykrytych obserwacji')));

  assert.equal(fullQueueReads, 2);
  assert.deepEqual(focusedIds, ['M2', 'M3']);
  assert.equal(reprojects, 0);
});

test('dirty navigation makes no request when cancelled and gates M2 until confirmed evidence', async () => {
  const m1 = mixedCase('M1', 11);
  const m2 = mixedCase('M2', 22);
  const pendingM2 = deferred<MixedPlayerFocusedCaseResponse>();
  const focusedIds: string[] = [];
  let fullQueueReads = 0;
  const view = renderPanel({
    getQueue: async () => { fullQueueReads += 1; return queue([m1, m2]); },
    getFocusedCase: async (_matchId, caseId) => {
      focusedIds.push(caseId);
      return pendingM2.promise;
    },
  });

  await waitFor(() => assert.ok(view.getByText('11 wykrytych obserwacji')));
  fireEvent.click(view.getByRole('button', { name: 'Corgi — nieznany' }));
  dom.window.confirm = () => false;
  fireEvent.click(view.getByRole('button', { name: 'Następny' }));
  assert.deepEqual(focusedIds, []);
  assert.equal(fullQueueReads, 1);

  dom.window.confirm = () => true;
  fireEvent.click(view.getByRole('button', { name: 'Następny' }));
  await waitFor(() => assert.deepEqual(focusedIds, ['M2']));
  assert.ok(view.getByText('11 wykrytych obserwacji'));
  await act(async () => pendingM2.resolve(focusedResponse('M2', 'current_blocking', m2)));
  await waitFor(() => assert.ok(view.getByText('22 wykrytych obserwacji')));
  assert.equal(fullQueueReads, 1);
});

test('empty reconciled Mixed queue routes from authoritative workflow without a focus error', async () => {
  const m1 = mixedCase('M1', 11);
  const m2 = mixedCase('M2', 22);
  let fullQueueReads = 0;
  let workflowReads = 0;
  let returnedToRequired = 0;
  const requiredWorkflow: ReviewWorkflow = {
    ...workflow,
    issues: { ...workflow.issues, blocking: 2, normal_blocking: 2, mixed_blocking: 0 },
  };
  const view = render(React.createElement(MixedPlayersReviewPanel, {
    match,
    workflow,
    onWorkflowChanged: () => undefined,
    onReturnToRequired: () => { returnedToRequired += 1; },
    reviewApi: {
      getQueue: async () => {
        fullQueueReads += 1;
        return fullQueueReads === 1 ? queue([m1, m2]) : queue([]);
      },
      getFocusedCase: async (_matchId, caseId) => focusedResponse(caseId, 'missing', null),
      getWorkflow: async () => { workflowReads += 1; return requiredWorkflow; },
    },
  }));

  await waitFor(() => assert.ok(view.getByText('11 wykrytych obserwacji')));
  fireEvent.click(view.getByRole('button', { name: 'Następny' }));
  await waitFor(() => assert.equal(returnedToRequired, 1));

  assert.equal(fullQueueReads, 2);
  assert.equal(workflowReads, 1);
  assert.equal(view.queryByText('Nie można bezpiecznie otworzyć wskazanego przypadku Mixed.'), null);
});

test('successful defer is never retried when its next case needs reconciliation', async () => {
  const m1 = mixedCase('M1', 11);
  const m2 = mixedCase('M2', 22);
  const m3 = mixedCase('M3', 33);
  let fullQueueReads = 0;
  let saves = 0;
  const focusedIds: string[] = [];
  const view = renderPanel({
    getQueue: async () => {
      fullQueueReads += 1;
      return fullQueueReads === 1 ? queue([m1, m2, m3]) : queue([m1, m3]);
    },
    saveResolution: async () => {
      saves += 1;
      return { saved_case: m1, semantic_decision_digest: 'saved-M1', recompute_deferred: true };
    },
    getFocusedCase: async (_matchId, caseId) => {
      focusedIds.push(caseId);
      return caseId === 'M2'
        ? focusedResponse('M2', 'not_in_mandatory_queue', null)
        : focusedResponse('M3', 'current_blocking', m3);
    },
  });

  await waitFor(() => assert.ok(view.getByText('11 wykrytych obserwacji')));
  fireEvent.click(view.getByRole('button', { name: 'Nie ma prostego podziału czasowego' }));
  await waitFor(() => assert.ok(view.getByText('33 wykrytych obserwacji')));

  assert.equal(saves, 1);
  assert.equal(fullQueueReads, 2);
  assert.deepEqual(focusedIds, ['M2', 'M3']);
});

test('resolve-now remains fail-closed and never reconciles to another Mixed case', async () => {
  let fullQueueReads = 0;
  const view = render(React.createElement(MixedPlayersReviewPanel, {
    match,
    workflow,
    focusCaseId: 'M-new',
    entryMode: 'resolve_now',
    onWorkflowChanged: () => undefined,
    reviewApi: {
      getQueue: async () => { fullQueueReads += 1; return queue([mixedCase('M-other', 44)]); },
      getFocusedCase: async (_matchId, caseId) => focusedResponse(caseId, 'missing', null),
    },
  }));

  await waitFor(() => assert.ok(view.getByText('Nie można bezpiecznie otworzyć wskazanego przypadku Mixed.')));
  assert.equal(fullQueueReads, 0);
  assert.equal(view.queryByText('44 wykrytych obserwacji'), null);
});
