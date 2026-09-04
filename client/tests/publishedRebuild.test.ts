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
