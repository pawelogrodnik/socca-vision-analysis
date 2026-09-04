import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { PublishedMatchReportPage } from '../src/components/PublishedMatchReportPage.tsx';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');

afterEach(() => cleanup());

function publishedDetail(title: string) {
  return {
    id: 'published-9c7485e4',
    source_match_id: '9c7485e4',
    title,
    package: { match: { id: '9c7485e4', title }, teams: [] },
    public_report: {
      id: 'published-9c7485e4',
      schema_version: '0.1.0',
      report_type: 'public_match_report',
      match: { id: '9c7485e4', title },
      teams: [],
      players: [],
    },
    teams: [],
    players: [],
  };
}

function renderPage() {
  return render(React.createElement(MemoryRouter, { initialEntries: ['/published/matches/published-9c7485e4/report'] }, React.createElement(Routes, null,
    React.createElement(Route, { path: '/published/matches/:matchId/report', element: React.createElement(PublishedMatchReportPage) }),
  )));
}

test('published report rebuilds through the dedicated endpoint and shows the new report', async () => {
  const calls: Array<{ path: string; method?: string }> = [];
  let resolveRebuild: ((value: unknown) => void) | null = null;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input, init) => {
    const path = String(input);
    calls.push({ path, method: init?.method });
    if (path.endsWith('/api/published/matches/published-9c7485e4')) return Response.json(publishedDetail('Stary tytuł'));
    if (path.endsWith('/published-9c7485e4/rebuild')) {
      return new Promise((resolve) => { resolveRebuild = resolve as (value: unknown) => void; });
    }
    throw new Error(`Unexpected ${path}`);
  }) as typeof fetch;
  try {
    const view = renderPage();
    await waitFor(() => assert.ok(view.getByRole('button', { name: 'Przebuduj publikację' })));
    assert.ok(view.getAllByText('Stary tytuł').length > 0);
    fireEvent.click(view.getByRole('button', { name: 'Przebuduj publikację' }));
    await waitFor(() => assert.ok(view.getByRole('button', { name: 'Przebudowuję...' })));
    assert.equal(view.getByRole('button', { name: 'Przebudowuję...' }).hasAttribute('disabled'), true);
    assert.ok(resolveRebuild);
    resolveRebuild(Response.json(publishedDetail('Nowy tytuł')));
    await waitFor(() => assert.ok(view.getByText('Publikacja została przebudowana z najnowszych danych lokalnych.')));
    assert.ok(view.getAllByText('Nowy tytuł').length > 0);
    assert.equal(view.queryAllByText('Stary tytuł').length, 0);
    const rebuild = calls.find((call) => call.path.endsWith('/published-9c7485e4/rebuild'));
    assert.equal(rebuild?.method, 'POST');
  } finally { globalThis.fetch = originalFetch; }
});

test('published report rebuild failure keeps the old report and allows retry', async () => {
  const calls: string[] = [];
  let attempts = 0;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input, init) => {
    const path = String(input);
    calls.push(`${init?.method || 'GET'} ${path}`);
    if (path.endsWith('/api/published/matches/published-9c7485e4')) return Response.json(publishedDetail('Stary tytuł'));
    if (path.endsWith('/published-9c7485e4/rebuild')) {
      attempts += 1;
      if (attempts === 1) {
        return Response.json({ detail: 'Local source match 9c7485e4 not found; publication left unchanged.' }, { status: 404 });
      }
      return Response.json(publishedDetail('Nowy tytuł'));
    }
    throw new Error(`Unexpected ${path}`);
  }) as typeof fetch;
  try {
    const view = renderPage();
    await waitFor(() => assert.ok(view.getByRole('button', { name: 'Przebuduj publikację' })));
    fireEvent.click(view.getByRole('button', { name: 'Przebuduj publikację' }));
    await waitFor(() => assert.ok(view.getByText(/Local source match 9c7485e4 not found/)));
    assert.ok(view.getAllByText('Stary tytuł').length > 0);
    assert.equal(view.getByRole('button', { name: 'Przebuduj publikację' }).hasAttribute('disabled'), false);
    fireEvent.click(view.getByRole('button', { name: 'Przebuduj publikację' }));
    await waitFor(() => assert.ok(view.getByText('Publikacja została przebudowana z najnowszych danych lokalnych.')));
    assert.equal(attempts, 2);
  } finally { globalThis.fetch = originalFetch; }
});

test('static fallback report renders without a rebuild action', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input) => {
    const path = String(input);
    if (path.endsWith('/api/published/matches/published-9c7485e4')) {
      return Response.json({ detail: 'Published match not found' }, { status: 404 });
    }
    if (path.endsWith('/published-9c7485e4/public_report.json')) {
      return Response.json(publishedDetail('Statyczny tytuł').public_report);
    }
    throw new Error(`Unexpected ${path}`);
  }) as typeof fetch;
  try {
    const view = renderPage();
    await waitFor(() => assert.ok(view.getAllByText('Statyczny tytuł').length > 0));
    assert.equal(view.queryByRole('button', { name: 'Przebuduj publikację' }), null);
    assert.equal(view.queryByRole('button', { name: 'Przebudowuję...' }), null);
  } finally { globalThis.fetch = originalFetch; }
});

test('published report keeps existing share and export actions', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input) => {
    const path = String(input);
    if (path.endsWith('/api/published/matches/published-9c7485e4')) return Response.json(publishedDetail('Tytuł'));
    throw new Error(`Unexpected ${path}`);
  }) as typeof fetch;
  try {
    const view = renderPage();
    await waitFor(() => assert.ok(view.getByRole('button', { name: 'Przebuduj publikację' })));
    assert.ok(view.getByRole('button', { name: 'Kopiuj link' }));
    assert.ok(view.getByRole('button', { name: 'Drukuj / PDF' }));
    assert.ok(view.getByRole('button', { name: 'Pobierz public report JSON' }));
  } finally { globalThis.fetch = originalFetch; }
});

function mergedDetail(title: string) {
  return {
    id: 'published-merged-abc123',
    source_match_id: 'match-group-abc',
    source_kind: 'merged',
    backing_group_id: 'match-group-abc',
    member_count: 2,
    member_published_ids: ['published-a', 'published-b'],
    capabilities: {
      rebuild_physical_publication: false,
      regenerate_report: true,
      refresh_to_latest: true,
      generate_video: true,
      external_video: true,
    },
    title,
    package: null,
    public_report: {
      id: 'published-merged-abc123',
      schema_version: '0.1.0',
      report_type: 'public_match_report',
      match: { id: 'published-merged-abc123', title },
      teams: [
        { team_label: 'A', team_id: 'team-corgi', team_name: 'Corgi', playing_time_sec: 900, total_distance_m: 1000, high_intensity_distance_m: 100, sprint_count: 5, avg_speed_kmh: 6, peak_speed_kmh: 20, pass_candidates: 10, same_team_pass_candidates: 10, turnover_or_interception_candidates: 0, progressive_pass_candidates: 2, accepted_passes: 8 },
        { team_label: 'B', team_id: 'team-verisk', team_name: 'Verisk', playing_time_sec: 900, total_distance_m: 800, high_intensity_distance_m: 80, sprint_count: 3, avg_speed_kmh: 5, peak_speed_kmh: 18, pass_candidates: 8, same_team_pass_candidates: 8, turnover_or_interception_candidates: 0, progressive_pass_candidates: 1, accepted_passes: 6 },
      ],
      players: [],
    },
    teams: [],
    players: [],
  };
}

function renderMergedPage() {
  return render(React.createElement(MemoryRouter, { initialEntries: ['/published/matches/published-merged-abc123/report'] }, React.createElement(Routes, null,
    React.createElement(Route, { path: '/published/matches/:matchId/report', element: React.createElement(PublishedMatchReportPage) }),
  )));
}

function mockMergedFetches(extra?: (path: string, init?: RequestInit) => Response | null) {
  const calls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = (async (input, init) => {
    const path = String(input);
    calls.push(`${init?.method || 'GET'} ${path}`);
    if (extra) {
      const response = extra(path, init);
      if (response) return response;
    }
    if (path.endsWith('/published-merged-abc123/video')) return Response.json({ group_id: 'match-group-abc', status: 'not_generated' });
    if (path.endsWith('/published-merged-abc123/external-video')) return Response.json({ group_id: 'match-group-abc', status: 'not_configured' });
    if (path.endsWith('/published-merged-abc123/refresh-preview')) return Response.json({ group_id: 'match-group-abc', status: 'current', members: [], blocking_reasons: [] });
    if (path.endsWith('/api/published/matches/published-merged-abc123')) return Response.json(mergedDetail('Scalony mecz'));
    throw new Error(`Unexpected ${path}`);
  }) as typeof fetch;
  return { calls, restore: () => { globalThis.fetch = originalFetch; } };
}

test('merged match uses the same report page without a physical rebuild action', async () => {
  const { calls, restore } = mockMergedFetches();
  try {
    const view = renderMergedPage();
    await waitFor(() => assert.ok(view.getAllByText('Scalony mecz').length > 0));
    // Same canonical content as a physical report.
    assert.ok(view.getByRole('heading', { name: 'Statystyki drużyn' }));
    // Merged lifecycle actions instead of the physical rebuild.
    assert.equal(view.queryByRole('button', { name: 'Przebuduj publikację' }), null);
    assert.ok(view.getByRole('button', { name: 'Regeneruj raport' }));
    assert.ok(view.getByRole('button', { name: 'Odśwież do najnowszych danych' }));
    assert.ok(view.getByText(/Scalony z 2 fragmentów/));
    assert.ok(calls.some((call) => call.endsWith('/api/published/matches/published-merged-abc123')));
  } finally { restore(); }
});

test('merged match regenerates its canonical report without repinning sources', async () => {
  const { calls, restore } = mockMergedFetches((path) => {
    if (path.endsWith('/published-merged-abc123/regenerate-report')) return Response.json(mergedDetail('Scalony mecz v2'));
    return null;
  });
  try {
    const view = renderMergedPage();
    await waitFor(() => assert.ok(view.getByRole('button', { name: 'Regeneruj raport' })));
    fireEvent.click(view.getByRole('button', { name: 'Regeneruj raport' }));
    await waitFor(() => assert.ok(view.getByText(/przebudowany z aktualnie przypiętych fragmentów/)));
    assert.ok(calls.some((call) => call === 'POST /api/published/matches/published-merged-abc123/regenerate-report'));
    assert.ok(!calls.some((call) => call.includes('refresh-to-latest')));
    assert.ok(view.getAllByText('Scalony mecz v2').length > 0);
  } finally { restore(); }
});

test('merged match refresh posts to the merged facade and keeps the same published id', async () => {
  const { calls, restore } = mockMergedFetches((path) => {
    if (path.endsWith('/published-merged-abc123/refresh-preview')) return Response.json({ group_id: 'match-group-abc', status: 'refreshable', members: [{ published_id: 'published-a', status: 'refreshable' }], blocking_reasons: [] });
    if (path.endsWith('/published-merged-abc123/refresh-to-latest')) return Response.json(mergedDetail('Scalony mecz'));
    return null;
  });
  try {
    const view = renderMergedPage();
    await waitFor(() => assert.equal(view.getByRole('button', { name: 'Odśwież do najnowszych danych' }).hasAttribute('disabled'), false));
    fireEvent.click(view.getByRole('button', { name: 'Odśwież do najnowszych danych' }));
    await waitFor(() => assert.ok(calls.some((call) => call === 'POST /api/published/matches/published-merged-abc123/refresh-to-latest')));
    assert.ok(!calls.some((call) => call.includes('/match-groups/')));
  } finally { restore(); }
});
