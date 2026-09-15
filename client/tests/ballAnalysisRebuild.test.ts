import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React from 'react';

import { BallAnalysisRebuildPanel } from '../src/components/BallAnalysisRebuildPanel.tsx';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/report?dev=1' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');
afterEach(() => cleanup());

const publishedId = 'published-merged-test';
const stale = {
  published_match_id: publishedId, status: 'stale', source_count: 2,
  sources: [
    { published_id: 'published-one', source_match_id: 'one', status: 'stale', current_effective_ball_track_digest: 'new', ball_track_input_digest: 'old', provenance: 'resolved_operator_projection' },
    { published_id: 'published-two', source_match_id: 'two', status: 'current', current_effective_ball_track_digest: 'same', ball_track_input_digest: 'same', provenance: 'automatic_legacy_fallback' },
  ],
} as const;

test('ball analysis action is dev-only and only a stale projection exposes one rebuild action', async () => {
  const hidden = render(React.createElement(BallAnalysisRebuildPanel, { publishedMatchId: publishedId, devAllowed: false }));
  assert.equal(hidden.queryByRole('button', { name: 'Przelicz analizę piłki' }), null);
  hidden.unmount();

  let rebuildCalls = 0;
  const fetchStatus = async () => stale;
  const requestRebuild = async () => {
    rebuildCalls += 1;
    return {
      published_match_id: publishedId,
      status: 'current',
      source_count: 2,
      sources: stale.sources.map((source) => ({
        ...source,
        status: 'current',
        ball_track_input_digest: source.current_effective_ball_track_digest,
      })),
      result: 'rebuilt',
    } as const;
  };
  const view = render(React.createElement(BallAnalysisRebuildPanel, {
    publishedMatchId: publishedId,
    devAllowed: true,
    fetchStatus,
    requestRebuild,
  }));
  await waitFor(() => {
    assert.ok(view.getByText('Analiza piłki wymaga przeliczenia.'));
    assert.equal(view.getByRole('button', { name: 'Przelicz analizę piłki' }).hasAttribute('disabled'), false);
  });
  const action = view.getByRole('button', { name: 'Przelicz analizę piłki' });
  fireEvent.click(action);
  assert.equal(action.hasAttribute('disabled'), true);
  assert.ok(action.textContent?.includes('Przeliczanie…'));
  await waitFor(() => assert.equal(rebuildCalls, 1));
  await waitFor(() => assert.ok(view.getByText('Analiza piłki aktualna.')));
});
