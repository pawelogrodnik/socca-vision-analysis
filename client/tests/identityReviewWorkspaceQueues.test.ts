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
const { ReviewedIdentityQueueTabs } = await import('../src/components/ReviewedIdentityQueueTabs.tsx');

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

test('technical Team-attribution failure explains recovery and exposes only server-authorized retry', async () => {
  const originalFetch = globalThis.fetch;
  const technical: ReviewWorkflow = {
    ...mandatoryWorkflow('exceptions'),
    status: 'error',
    required_action: { type: 'coverage_evidence_technical_failure', step_id: 'exceptions' },
    issues: {
      blocking: 0,
      normal_blocking: 0,
      mixed_blocking: 0,
      important: 0,
      optional: 0,
      coverage_readiness_blocked: true,
      team_attribution_evidence_technical_failure: true,
      coverage_readiness: {
        status: 'incomplete',
        policy_version: 'test',
        allows_finalize: false,
        roster_scope: {},
        blockers: [{ code: 'team_attribution_evidence_technical_failure' }],
        team_attribution_residual: {
          status: 'technical_evidence_failure',
          units: 1,
          observations: 3,
          residual_budget_observations: 10,
          within_tolerance: true,
          evidence_status_counts: { source_video_unavailable: 1 },
        },
      },
    },
    blockers: [{
      code: 'team_attribution_evidence_technical_failure',
      step_id: 'exceptions',
      user_actionable: true,
      details: {},
    }],
    allowed_actions: ['retry_review_recompute'],
    mandatory_operator_review_complete: false,
    data_quality_ready_for_output: false,
  };
  installWorkspaceFetch(technical);
  try {
    const view = renderWorkspace(technical);
    await waitFor(() => assert.ok(view.getByRole('button', { name: 'Spróbuj ponownie' })));
    assert.ok(view.getByText(/Nie udało się przygotować bezpiecznych widoków/));
    assert.equal(view.queryByText('Nie udało się przygotować kolejnego kroku review.'), null);
    assert.equal(view.queryByText(/Wymagany Review zakończony/), null);
    assert.equal(view.queryByText(/Wymagane przypadki =/), null);
    assert.equal(view.queryByRole('button', { name: /Przygotuj wynik/ }), null);
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
    fireEvent.click(view.getByRole('button', { name: /Wymagane przypadki/ }));
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

test('a remediation blocker remains separate from an empty Required queue badge', async () => {
  const remediation: ReviewWorkflow = {
    ...mandatoryWorkflow('exceptions'),
    status: 'error',
    issues: {
      ...mandatoryWorkflow('exceptions').issues,
      blocking: 0,
      normal_blocking: 0,
      required_queue: { count: 0, source_keys_digest: 'empty-required-queue' },
      mixed_blocking: 0,
      coverage_readiness_blocked: true,
      team_attribution_evidence_not_materialized: true,
      coverage_readiness: {
        status: 'incomplete',
        allows_finalize: false,
        blockers: [{ code: 'team_attribution_evidence_not_materialized' }],
        team_attribution_residual: { status: 'materialization_required' },
      },
    },
    blockers: [{
      code: 'identity_coverage_unresolved_without_reviewable_evidence',
      step_id: 'exceptions',
      user_actionable: true,
      details: {},
    }],
    allowed_actions: ['retry_review_recompute'],
    required_action: { type: 'retry_review_recompute', step_id: 'exceptions' },
  };
  const originalFetch = globalThis.fetch;
  try {
    const tabs = render(React.createElement(ReviewedIdentityQueueTabs, {
      workflow: remediation,
      activeQueue: 'required',
      onSelect: () => undefined,
    }));
    assert.match(tabs.getByRole('button', { name: /Wymagane przypadki/ }).textContent || '', /Wymagane przypadki 0/);

    const workspace = renderWorkspace(remediation);
    await waitFor(() => assert.match(
      workspace.container.textContent || '',
      /System musi jeszcze przygotować bezpieczne widoki/,
    ));
  } finally {
    globalThis.fetch = originalFetch;
  }
});
