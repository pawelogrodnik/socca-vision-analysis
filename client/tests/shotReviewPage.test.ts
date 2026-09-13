import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { RedesignedPublishedMatchReportPage } from '../src/components/RedesignedPublishedMatchReportPage.tsx';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');
const originalFetch = globalThis.fetch;

afterEach(() => { cleanup(); globalThis.fetch = originalFetch; });

function renderPage() {
  return render(React.createElement(MemoryRouter, { initialEntries: ['/matches/published-shot-review/report?dev=1'] }, React.createElement(Routes, null,
    React.createElement(Route, { path: '/matches/:matchId/report', element: React.createElement(RedesignedPublishedMatchReportPage) }),
  )));
}

test('Shot Review revision conflict reloads the server state and does not silently overwrite it', async () => {
  let shotStateReads = 0;
  globalThis.fetch = (async (input, init) => {
    const path = String(input);
    if (path.endsWith('/api/published/matches/published-shot-review')) {
      return Response.json({ source_kind: 'physical', public_report: {
        schema_version: 'public_match_report.v1', generated_at: 'now', id: 'published-shot-review', source_match_id: 'source', report_type: 'public_match_report',
        match: { id: 'published-shot-review', title: 'Raport', duration_sec: 100 }, teams: [{ team_id: 'corgi', team_name: 'Corgi' }], players: [], key_moments: { schema_version: 'key-moments.v1', moments: [] },
      } });
    }
    if (path.endsWith('/external-video')) return Response.json({ status: 'not_configured' });
    if (path.endsWith('/key-moments/editor')) return Response.json({ key_moment_editor_allowed: true, revision: 'km-r1', moments: [], suggestions: { status: 'ready', candidates: [], unreviewed_count: 0 } });
    if (path.endsWith('/shot-review/editor')) {
      shotStateReads += 1;
      return Response.json({ published_id: 'published-shot-review', revision: shotStateReads === 1 ? 'shot-r1' : 'shot-r2', has_editorial_sidecar: true, canonical_shots: [{ shot_id: 'shot-1', time_sec: 10, team_id: 'corgi', outcome: 'blocked', player_id: null, origin: 'manual', location_m: { x: 4, y: 5 }, location_source: 'ball' }], candidate_generation_digest: 'digest', candidate_count: 0, accepted_count: 0, rejected_count: 0, unreviewed_count: 0, unreviewed_suggestions: [] });
    }
    if (path.endsWith('/shot-review/editor/shots/shot-1') && init?.method === 'PUT') {
      return Response.json({ detail: { code: 'shot_review_revision_conflict', detail: 'stale' } }, { status: 409 });
    }
    throw new Error(`Unexpected ${path}`);
  }) as typeof fetch;

  const view = renderPage();
  await waitFor(() => assert.ok(view.getByRole('tab', { name: 'Strzały' })));
  fireEvent.click(view.getByRole('tab', { name: 'Strzały' }));
  fireEvent.click(view.getByRole('button', { name: 'Edytuj' }));
  fireEvent.click(view.getByRole('button', { name: 'Zapisz strzał' }));
  await waitFor(() => assert.ok(view.getByText('Stan Shot Review zmienił się. Odświeżono najnowszą wersję.')));
  assert.ok(shotStateReads >= 2);
});
