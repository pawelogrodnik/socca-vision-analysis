import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import { JSDOM } from 'jsdom';
import React from 'react';

import type { Match, ReviewedIdentityReviewUnit, ReviewWorkflow } from '../src/types.ts';

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

function requiredUnit(subject: string): ReviewedIdentityReviewUnit {
  return {
    candidate_subject_id: subject,
    tracklet_ids: ['track-1'],
    tracklet_count: 1,
    source_team_label: 'A',
    effective_team_label: 'A',
    frame_start: 1,
    frame_end: 2,
    detected_frame_count: 2,
    detected_observation_count: 2,
    detected_time_sec: 0.1,
    current_resolution_status: 'pending_high_priority',
    priority: 'high',
    reason_codes: [],
    visual_evidence: {
      status: 'ready',
      anchor_crops: [{
        anchor_crop_id: `crop-${subject}`,
        artifact: `reviewed/${subject}.jpg`,
        frame: 1,
        time_sec: 0.1,
      }],
    },
  };
}

function progressFor(units: ReviewedIdentityReviewUnit[], total = units.length) {
  return {
    next_cases: units,
    pagination: {
      offset: 0,
      total_remaining: total,
      global_total_remaining: total,
      has_more: false,
    },
    filters: { counts: { all: total } },
  };
}

function workflowWithCounts(normal: number, mixed: number): ReviewWorkflow {
  return {
    ...mandatoryWorkflow(normal > 0 ? 'exceptions' : 'mixed_players'),
    phase: normal > 0 ? 'exceptions' : 'mixed_players',
    current_step_id: normal > 0 ? 'exceptions' : 'mixed_players',
    issues: {
      blocking: normal + mixed,
      normal_blocking: normal,
      mixed_blocking: mixed,
      important: normal + mixed,
      optional: 0,
    },
    allowed_actions: [
      ...(normal > 0 ? ['review_identity_issue' as const] : []),
      ...(mixed > 0 ? ['review_mixed_players' as const] : []),
    ],
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => { resolve = nextResolve; });
  return { promise, resolve };
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
    mandatory_operator_review_complete: true,
    data_quality_ready_for_output: false,
  };
  installWorkspaceFetch(technical);
  try {
    const view = renderWorkspace(technical);
    await waitFor(() => assert.ok(view.getByRole('button', { name: 'Spróbuj ponownie' })));
    assert.ok(view.getByText(/Nie udało się przygotować bezpiecznych widoków/));
    assert.ok(view.getByText(/Sprawdź dostępność pliku wideo/));
    assert.equal(view.queryByText('Nie udało się przygotować kolejnego kroku review.'), null);
    assert.ok(view.getByText(/Wymagany Review zakończony/));
    assert.equal(view.queryByText(/Wymagane przypadki =/), null);
    assert.equal(view.queryByRole('button', { name: /Przygotuj wynik/ }), null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('terminal technical Team-attribution failure does not promise an unavailable retry', async () => {
  const originalFetch = globalThis.fetch;
  const retryable: ReviewWorkflow = {
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
          units: 50,
          observations: 6814,
          residual_budget_observations: 14982,
          within_tolerance: true,
          evidence_status_counts: { team_attribution_evidence_recovery_incomplete: 50 },
        },
      },
    },
    blockers: [{
      code: 'team_attribution_evidence_technical_failure',
      step_id: 'exceptions',
      user_actionable: false,
      details: {},
    }],
    allowed_actions: [],
    mandatory_operator_review_complete: true,
    data_quality_ready_for_output: false,
  };
  installWorkspaceFetch(retryable);
  try {
    const view = renderWorkspace(retryable);
    await waitFor(() => assert.ok(view.getByText(/Wymagany Review zakończony/)));
    assert.ok(view.getByText(/Nie ma już kolejnych bezpiecznych decyzji manualnych/));
    assert.ok(view.getByText(/Automatyczne ponowienie Review nie może naprawić/));
    assert.equal(view.queryByRole('button', { name: 'Spróbuj ponownie' }), null);
    assert.equal(view.queryByText(/Sprawdź dostępność pliku wideo/), null);
    assert.equal(view.queryByRole('button', { name: /Przygotuj wynik/ }), null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('a pending recompute generation recovers once before mounting any mandatory queue', async () => {
  const originalFetch = globalThis.fetch;
  const requests: string[] = [];
  const pending: ReviewWorkflow = {
    ...mandatoryWorkflow('exceptions'),
    status: 'error',
    required_action: { type: 'retry_review_recompute', step_id: 'exceptions' },
    issues: {
      blocking: 0,
      normal_blocking: 0,
      mixed_blocking: 0,
      important: 0,
      optional: 0,
    },
    freshness: {
      reviewed_identity_current: true,
      reviewed_stats_current: false,
      reviewed_output_current: false,
      qa_approval_current: false,
      review_progress_current: false,
      review_progress_reason: 'review_progress_recompute_required',
      review_progress_recompute_generation: 'mixed-structural-generation',
    },
    blockers: [{
      code: 'review_progress_recompute_required',
      step_id: 'exceptions',
      user_actionable: true,
      details: {},
    }],
    allowed_actions: ['retry_review_recompute'],
  };
  const recovered: ReviewWorkflow = {
    ...pending,
    phase: 'ready_to_finalize',
    status: 'ready',
    current_step_id: 'finalize',
    required_action: { type: 'finalize_identity', step_id: 'finalize' },
    freshness: {
      ...pending.freshness,
      review_progress_current: true,
      review_progress_reason: null,
      review_progress_recompute_generation: null,
    },
    blockers: [],
    allowed_actions: ['finalize_identity'],
  };
  globalThis.fetch = async (input) => {
    const url = String(input);
    requests.push(url);
    const body = url.includes('/retry-recompute')
      ? { workflow: recovered }
      : url.includes('/review-workflow')
        ? pending
        : {};
    return new Response(JSON.stringify(body), {
      headers: { 'Content-Type': 'application/json' },
    });
  };
  try {
    const view = renderWorkspace(pending);
    await waitFor(() => assert.equal(
      requests.filter((url) => url.includes('/retry-recompute')).length,
      1,
    ));
    await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Wymagany przegląd zakończony' })));
    assert.equal(
      requests.some((url) => url.includes('/reviewed-identity/review-progress')),
      false,
    );
    assert.equal(
      requests.some((url) => url.includes('/reviewed-identity/mixed-players')),
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

test('last Required save keeps Mixed unmounted until completion synchronization returns Required', async () => {
  const originalFetch = globalThis.fetch;
  const requests: string[] = [];
  const completion = deferred<Response>();
  const initial = workflowWithCounts(1, 0);
  const interim = workflowWithCounts(0, 19);
  const authoritative = workflowWithCounts(30, 19);
  const subject = 'completion-race-required';
  globalThis.fetch = async (input) => {
    const url = String(input);
    requests.push(url);
    if (url.includes('/reviewed-identity/corrections/context')) {
      return new Response(JSON.stringify({
        candidate_subject_id: subject,
        team_label: 'A',
        source_team_label: 'A',
        effective_team_label: 'A',
        available_team_labels: ['A', 'B'],
        tracklet_ids: ['track-1'],
        review_card_key: null,
        roster_options: [],
        slot_options: [],
        current_decision: null,
        semantic_decision_digest: 'decision',
        action_capabilities: { referee: { allowed: true } },
        review_state_version: 1,
      }), { headers: { 'Content-Type': 'application/json' } });
    }
    if (url.endsWith('/reviewed-identity/corrections')) {
      return new Response(JSON.stringify({
        saved_decision: {}, effective_action: 'referee', allocated_stable_slot_id: null,
        semantic_decision_digest: 'decision', recompute_deferred: true, workflow: interim,
      }), { headers: { 'Content-Type': 'application/json' } });
    }
    if (url.endsWith('/reviewed-identity/corrections/finalize')) return completion.promise;
    if (url.includes('/reviewed-identity/review-progress')) {
      return new Response(JSON.stringify(
        requests.some((request) => request.endsWith('/reviewed-identity/corrections/finalize'))
          ? progressFor([requiredUnit('authoritative-required')], 30)
          : progressFor([requiredUnit(subject)], 1),
      ), { headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/review-workflow')) return new Response(JSON.stringify(initial), { headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/identity-roster-subject-review')) return new Response(JSON.stringify({ cards: [] }), { headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/reviewed-identity/mixed-players')) return new Response(JSON.stringify({ cases: [] }), { headers: { 'Content-Type': 'application/json' } });
    return new Response(JSON.stringify({}), { headers: { 'Content-Type': 'application/json' } });
  };
  try {
    const view = renderWorkspace(initial);
    await waitFor(() => assert.ok(view.getByRole('radio', { name: 'Sędzia' })));
    fireEvent.click(view.getByRole('radio', { name: 'Sędzia' }));
    await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Zapisz + następny' })); });
    await waitFor(() => assert.ok(view.getByRole('status', { name: '' }).textContent?.includes('Synchronizuję Review')));
    assert.equal(requests.some((url) => url.includes('/reviewed-identity/mixed-players')), false);
    assert.equal(view.getByRole('button', { name: /Zmieszani gracze/ }).hasAttribute('disabled'), true);

    await act(async () => {
      completion.resolve(new Response(JSON.stringify({
        workflow: authoritative,
        reviewed_identity: {}, review_progress: {}, recompute_deferred: false,
      }), { headers: { 'Content-Type': 'application/json' } }));
    });
    await waitFor(() => assert.ok(view.getByRole('button', { name: /Wymagane przypadki 30/ })));
    assert.equal(requests.some((url) => url.includes('/reviewed-identity/mixed-players')), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('Mixed mounts only after completion synchronization authoritatively leaves no Required cases', async () => {
  const originalFetch = globalThis.fetch;
  const requests: string[] = [];
  const completion = deferred<Response>();
  const initial = workflowWithCounts(1, 0);
  const interim = workflowWithCounts(0, 19);
  const authoritative = workflowWithCounts(0, 19);
  const subject = 'completion-race-mixed';
  globalThis.fetch = async (input) => {
    const url = String(input);
    requests.push(url);
    if (url.includes('/reviewed-identity/corrections/context')) {
      return new Response(JSON.stringify({
        candidate_subject_id: subject, team_label: 'A', source_team_label: 'A', effective_team_label: 'A',
        available_team_labels: ['A', 'B'], tracklet_ids: ['track-1'], review_card_key: null,
        roster_options: [], slot_options: [], current_decision: null, semantic_decision_digest: 'decision',
        action_capabilities: { referee: { allowed: true } }, review_state_version: 1,
      }), { headers: { 'Content-Type': 'application/json' } });
    }
    if (url.endsWith('/reviewed-identity/corrections')) {
      return new Response(JSON.stringify({
        saved_decision: {}, effective_action: 'referee', allocated_stable_slot_id: null,
        semantic_decision_digest: 'decision', recompute_deferred: true, workflow: interim,
      }), { headers: { 'Content-Type': 'application/json' } });
    }
    if (url.endsWith('/reviewed-identity/corrections/finalize')) return completion.promise;
    if (url.includes('/reviewed-identity/review-progress')) {
      return new Response(JSON.stringify(
        requests.some((request) => request.endsWith('/reviewed-identity/corrections/finalize'))
          ? progressFor([], 0)
          : progressFor([requiredUnit(subject)], 1),
      ), { headers: { 'Content-Type': 'application/json' } });
    }
    if (url.includes('/review-workflow')) return new Response(JSON.stringify(initial), { headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/identity-roster-subject-review')) return new Response(JSON.stringify({ cards: [] }), { headers: { 'Content-Type': 'application/json' } });
    if (url.includes('/reviewed-identity/mixed-players')) return new Response(JSON.stringify({
      schema_version: '1.0.0', mode: 'mixed', match_id: match.id,
      summary: { total: 0, unresolved: 0, resolved: 0, complex_unresolved: 0 },
      assignment_options: { roster: [], slots: [] }, cases: [],
    }), { headers: { 'Content-Type': 'application/json' } });
    return new Response(JSON.stringify({}), { headers: { 'Content-Type': 'application/json' } });
  };
  try {
    const view = renderWorkspace(initial);
    await waitFor(() => assert.ok(view.getByRole('radio', { name: 'Sędzia' })));
    fireEvent.click(view.getByRole('radio', { name: 'Sędzia' }));
    await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Zapisz + następny' })); });
    await waitFor(() => assert.ok(view.getByText(/Synchronizuję Review/)));
    assert.equal(requests.some((url) => url.includes('/reviewed-identity/mixed-players')), false);

    await act(async () => {
      completion.resolve(new Response(JSON.stringify({
        workflow: authoritative,
        reviewed_identity: {}, review_progress: {}, recompute_deferred: false,
      }), { headers: { 'Content-Type': 'application/json' } }));
    });
    await waitFor(() => assert.ok(requests.some((url) => url.includes('/reviewed-identity/mixed-players'))));
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

test('retry replaces generic materialization copy with the backend technical outcome', async () => {
  const remediation: ReviewWorkflow = {
    ...mandatoryWorkflow('exceptions'),
    status: 'error',
    issues: {
      ...mandatoryWorkflow('exceptions').issues,
      blocking: 0,
      normal_blocking: 0,
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
    mandatory_operator_review_complete: true,
    data_quality_ready_for_output: false,
  };
  const technical: ReviewWorkflow = {
    ...remediation,
    issues: {
      ...remediation.issues,
      team_attribution_evidence_not_materialized: false,
      team_attribution_evidence_technical_failure: true,
      coverage_readiness: {
        status: 'incomplete',
        allows_finalize: false,
        blockers: [{ code: 'team_attribution_evidence_technical_failure' }],
        team_attribution_residual: { status: 'technical_evidence_failure' },
      },
    },
    blockers: [{
      code: 'team_attribution_evidence_technical_failure',
      step_id: 'exceptions',
      user_actionable: true,
      details: {},
    }],
    required_action: { type: 'coverage_evidence_technical_failure', step_id: 'exceptions' },
  };
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async (input) => {
      const url = String(input);
      const body = url.includes('/retry-recompute')
        ? { workflow: technical }
        : url.includes('/review-workflow')
          ? remediation
          : {};
      return new Response(JSON.stringify(body), {
        headers: { 'Content-Type': 'application/json' },
      });
    };
    const view = renderWorkspace(remediation);
    await waitFor(() => assert.ok(view.getByRole('button', { name: 'Spróbuj ponownie' })));
    await act(async () => {
      fireEvent.click(view.getByRole('button', { name: 'Spróbuj ponownie' }));
    });
    await waitFor(() => assert.ok(view.getByText(/Nie udało się przygotować bezpiecznych widoków/)));
    assert.equal(view.queryByText(/System musi jeszcze przygotować bezpieczne widoki/), null);
    assert.equal(view.queryByRole('button', { name: /Przygotuj wynik/ }), null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
