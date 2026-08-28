import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import { JSDOM } from 'jsdom';
import React from 'react';

import type { Match, ReviewWorkflow } from '../src/types.ts';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { act, cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');
const { IdentityReviewWorkspace } = await import('../src/components/IdentityReviewWorkspace.tsx');

afterEach(() => cleanup());

const match = {
  id: 'm1',
  title: 'Corgi – Verisk',
  teams: [
    { id: 'team-a', name: 'Corgi', players: [] },
    { id: 'team-b', name: 'Verisk', players: [] },
  ],
} as unknown as Match;

function mandatoryWorkflow(
  phase: 'exceptions' | 'mixed_players',
): ReviewWorkflow {
  return {
    schema_version: '1.0.0',
    match_id: match.id,
    available: true,
    phase,
    status: 'action_required',
    current_step_id: phase,
    review_complete: false,
    can_enter_report: false,
    can_publish: false,
    steps: [],
    required_action: null,
    issues: {
      blocking: 2,
      normal_blocking: 1,
      mixed_blocking: 1,
      important: 2,
      optional: 0,
    },
    freshness: {
      reviewed_identity_current: true,
      reviewed_stats_current: false,
      reviewed_output_current: false,
      qa_approval_current: false,
    },
    blockers: [],
    allowed_actions: ['review_identity_issue', 'review_mixed_players'],
  };
}

function emptyProgress() {
  return {
    next_cases: [],
    pagination: {
      offset: 0,
      total_remaining: 0,
      global_total_remaining: 0,
      has_more: false,
    },
    filters: { counts: { all: 0 } },
  };
}

function installWorkspaceFetch(workflow: ReviewWorkflow): string[] {
  const requests: string[] = [];
  globalThis.fetch = async (input) => {
    const url = String(input);
    requests.push(url);
    const body = url.includes('/review-workflow')
      ? workflow
      : url.includes('/identity-roster-subject-review')
        ? { cards: [] }
        : url.includes('/reviewed-identity/review-progress')
          ? emptyProgress()
          : url.includes('/reviewed-identity/mixed-players')
            ? { cases: [] }
            : {};
    return new Response(JSON.stringify(body), {
      headers: { 'Content-Type': 'application/json' },
    });
  };
  return requests;
}

function renderWorkspace(workflow: ReviewWorkflow) {
  return render(React.createElement(IdentityReviewWorkspace, {
    match,
    initialWorkflow: workflow,
    onWorkflowChanged: () => undefined,
    onOpenReport: () => undefined,
  }));
}

test('an initially Mixed workflow mounts only Mixed and never starts Required progress', async () => {
  const originalFetch = globalThis.fetch;
  const requests = installWorkspaceFetch(mandatoryWorkflow('mixed_players'));
  try {
    renderWorkspace(mandatoryWorkflow('mixed_players'));
    await waitFor(() => assert.ok(requests.some((url) => url.includes('/mixed-players'))));
    assert.equal(
      requests.some((url) => url.includes('review-progress') && url.includes('queue=required')),
      false,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('StrictMode remount shares one pending Required progress request', async () => {
  const originalFetch = globalThis.fetch;
  let progressCalls = 0;
  let resolveProgress!: (response: Response) => void;
  const initial = mandatoryWorkflow('exceptions');
  globalThis.fetch = async (input) => {
    const url = String(input);
    if (url.includes('review-progress') && url.includes('queue=required')) {
      progressCalls += 1;
      return new Promise<Response>((resolve) => { resolveProgress = resolve; });
    }
    const body = url.includes('/review-workflow')
      ? initial
      : url.includes('/identity-roster-subject-review')
        ? { cards: [] }
        : {};
    return new Response(JSON.stringify(body), {
      headers: { 'Content-Type': 'application/json' },
    });
  };
  try {
    render(React.createElement(
      React.StrictMode,
      null,
      React.createElement(IdentityReviewWorkspace, {
        match,
        initialWorkflow: initial,
        onWorkflowChanged: () => undefined,
        onOpenReport: () => undefined,
      }),
    ));
    await waitFor(() => assert.equal(progressCalls, 1));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    assert.equal(progressCalls, 1);

    resolveProgress(new Response(JSON.stringify(emptyProgress()), {
      headers: { 'Content-Type': 'application/json' },
    }));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('an explicit Mixed to Required switch loads Required normally', async () => {
  const originalFetch = globalThis.fetch;
  const requests = installWorkspaceFetch(mandatoryWorkflow('mixed_players'));
  try {
    const view = renderWorkspace(mandatoryWorkflow('mixed_players'));
    await waitFor(() => assert.ok(requests.some((url) => url.includes('/mixed-players'))));
    fireEvent.click(view.getByRole('button', { name: /Pozostałe przypadki/ }));
    await waitFor(() => assert.ok(
      requests.some((url) => url.includes('review-progress') && url.includes('queue=required')),
    ));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('switching Required to Mixed starts no additional Required progress request', async () => {
  const originalFetch = globalThis.fetch;
  const requests = installWorkspaceFetch(mandatoryWorkflow('exceptions'));
  try {
    const view = renderWorkspace(mandatoryWorkflow('exceptions'));
    await waitFor(() => assert.ok(
      requests.some((url) => url.includes('review-progress') && url.includes('queue=required')),
    ));
    const requiredBefore = requests.filter((url) => (
      url.includes('review-progress') && url.includes('queue=required')
    )).length;
    fireEvent.click(view.getByRole('button', { name: /Zmieszani gracze/ }));
    await waitFor(() => assert.ok(requests.some((url) => url.includes('/mixed-players'))));
    assert.equal(
      requests.filter((url) => url.includes('review-progress') && url.includes('queue=required')).length,
      requiredBefore,
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
