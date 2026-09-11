import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React from 'react';

import { MergedSourceDataRebuildPanel } from '../src/components/MergedSourceDataRebuildPanel.tsx';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/report?dev=1' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');

afterEach(() => cleanup());

const mergedId = 'published-merged-00000000-0000-4000-8000-000000000001';
const source = {
  published_id: 'published-source-one', source_match_id: 'source-one', classification: 'safe_stats_only',
  derived_data_status: 'stale', video_disposition: 'historical_preserved', qa_disposition: 'historical_preserved',
};

function preflight() {
  return {
    status: 'ready', merged_published_match_id: mergedId, group_id: 'group-one', source_count: 1,
    sources: [source], blocking_reasons: [],
  };
}

test('source-data rebuild action is absent outside existing dev presentation', () => {
  const view = render(React.createElement(MergedSourceDataRebuildPanel, {
    mergedId, devAllowed: false, onReportUpdated: () => undefined,
  }));
  assert.equal(view.queryByRole('button', { name: 'Przebuduj mecze źródłowe' }), null);
});

test('dev panel shows preflight historical-video disposition and submits one backend job', async () => {
  const calls: Array<{ path: string; method?: string }> = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input, init) => {
    const path = String(input);
    calls.push({ path, method: init?.method });
    if (path.endsWith('/rebuild-source-data/preview')) return Response.json(preflight());
    if (path.endsWith('/rebuild-source-data/status')) return Response.json({ status: 'idle', merged_published_match_id: mergedId, group_id: 'group-one', preflight: preflight() });
    if (path.endsWith('/rebuild-source-data') && init?.method === 'POST') return Response.json({
      status: 'queued', merged_published_match_id: mergedId, group_id: 'group-one', preflight: preflight(), sources: [source],
      progress: { phase: 'preflight', source_index: 0, source_total: 1 },
    });
    throw new Error(`Unexpected ${path}`);
  }) as typeof fetch;
  try {
    const view = render(React.createElement(MergedSourceDataRebuildPanel, {
      mergedId, devAllowed: true, onReportUpdated: () => undefined,
    }));
    await waitFor(() => assert.ok(view.getByText(/Review video pozostanie historyczne/)));
    fireEvent.click(view.getByRole('button', { name: 'Przebuduj mecze źródłowe' }));
    await waitFor(() => assert.ok(calls.some((call) => call.path.endsWith('/rebuild-source-data') && call.method === 'POST')));
    assert.ok(view.getByText(/Mecz 0\/1/));
    assert.equal(calls.filter((call) => call.path.endsWith('/rebuild-source-data') && call.method === 'POST').length, 1);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('durable active job renders meaningful source and phase progress without a fabricated percentage', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input) => {
    const path = String(input);
    if (path.endsWith('/rebuild-source-data/preview')) return Response.json(preflight());
    if (path.endsWith('/rebuild-source-data/status')) return Response.json({
      status: 'running', merged_published_match_id: mergedId, group_id: 'group-one', preflight: preflight(), sources: [source],
      progress: { phase: 'building_stats', source_index: 1, source_total: 3, source_match_id: 'source-one' },
    });
    throw new Error(`Unexpected ${path}`);
  }) as typeof fetch;
  try {
    const view = render(React.createElement(MergedSourceDataRebuildPanel, {
      mergedId, devAllowed: true, onReportUpdated: () => undefined,
    }));
    await waitFor(() => assert.ok(view.getByText('Przeliczanie statystyk')));
    assert.ok(view.getByText('Mecz 1/3 — source-one'));
    assert.equal(view.container.textContent?.includes('%'), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('progress renders real builder units and a backend failure reason', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input) => {
    const path = String(input);
    if (path.endsWith('/rebuild-source-data/preview')) return Response.json(preflight());
    if (path.endsWith('/rebuild-source-data/status')) return Response.json({
      status: 'failed', merged_published_match_id: mergedId, group_id: 'group-one', preflight: preflight(), sources: [source],
      progress: { phase: 'failed', source_index: 2, source_total: 3, processed_units: 500, total_units: 1000 },
      failure: { code: 'review_progress_not_ready', published_id: 'published-source-two', detail: 'Źródło source-two wymaga Review.' },
    });
    throw new Error(`Unexpected ${path}`);
  }) as typeof fetch;
  try {
    const view = render(React.createElement(MergedSourceDataRebuildPanel, {
      mergedId, devAllowed: true, onReportUpdated: () => undefined,
    }));
    await waitFor(() => assert.ok(view.getByText('500 / 1000')));
    assert.ok(view.container.textContent?.includes('published-source-two: Źródło source-two wymaga Review.'));
  } finally {
    globalThis.fetch = originalFetch;
  }
});
