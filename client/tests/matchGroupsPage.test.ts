import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React from 'react';
import { BrowserRouter } from 'react-router-dom';

import { MatchGroupsPage } from '../src/components/MatchGroupsPage.tsx';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/match-groups' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');

afterEach(() => cleanup());

const sources = [
  { id: 'physical-a', source_match_id: 'a', title: 'Pierwsza połowa', match_date: '2026-08-20', teams: ['Corgi', 'Verisk'], analyzed_duration_sec: 600, status: 'published', report_type: 'public_match_report' },
  { id: 'physical-b', source_match_id: 'b', title: 'Końcówka', match_date: '2026-08-20', teams: ['Corgi', 'Verisk'], analyzed_duration_sec: 300, status: 'published', report_type: 'public_match_report' },
];

test('match-group page selects physical sources, orders IDs and submits no statistics', async () => {
  const calls: Array<{ path: string; body?: unknown }> = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const path = String(input);
    calls.push({ path, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    if (path.endsWith('/eligible-sources')) return Response.json(sources);
    if (path.endsWith('/match-groups') && !init?.method) return Response.json([]);
    if (path.endsWith('/preview')) return Response.json({ status: 'compatible', compatibility: { status: 'compatible', blocking_reasons: [] }, timing: { analyzed_duration_sec: 900, timeline_span_sec: 900, mapping: 'ordered' }, members: [] });
    if (path.endsWith('/match-groups')) return Response.json({ group: { group_id: 'new-group', metadata: {}, members: [], timing: { analyzed_duration_sec: 900, timeline_span_sec: 900, mapping: 'ordered' }, compatibility: { status: 'compatible', blocking_reasons: [] } }, validation: { status: 'compatible', blocking_reasons: [] }, report: { report_type: 'public_aggregate_match_report' } });
    throw new Error(`Unexpected ${path}`);
  };
  try {
    const view = render(React.createElement(BrowserRouter, null, React.createElement(MatchGroupsPage)));
    await waitFor(() => assert.ok(view.getByText(/Pierwsza połowa/)));
    const checks = view.getAllByRole('checkbox');
    fireEvent.click(checks[0]);
    fireEvent.click(checks[1]);
    await waitFor(() => assert.ok(view.getByText(/Zgodne źródła/)));
    fireEvent.click(view.getAllByRole('button', { name: 'Przenieś wyżej' })[1]);
    await waitFor(() => assert.equal(view.getByRole('button', { name: 'Utwórz scalony raport' }).hasAttribute('disabled'), false));
    fireEvent.click(view.getByRole('button', { name: 'Utwórz scalony raport' }));
    await waitFor(() => assert.ok(calls.some((call) => call.path.endsWith('/match-groups') && call.body)));
    const create = calls.filter((call) => call.path.endsWith('/match-groups') && call.body).at(-1) as { body: { member_published_ids: string[]; metadata: { title: string } } };
    assert.deepEqual(create.body.member_published_ids, ['physical-b', 'physical-a']);
    assert.deepEqual(Object.keys(create.body).sort(), ['member_published_ids', 'metadata']);
    assert.deepEqual(create.body.metadata, { title: '' });
  } finally { globalThis.fetch = originalFetch; }
});
