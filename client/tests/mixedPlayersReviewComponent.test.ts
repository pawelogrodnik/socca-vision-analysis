import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';

import { JSDOM } from 'jsdom';
import React from 'react';

import { MixedPlayersReviewPanel, type MixedPlayersReviewApi } from '../src/components/MixedPlayersReviewPanel.tsx';
import { ReviewedIdentityCorrectionForm } from '../src/components/ReviewedIdentityCorrectionForm.tsx';
import { ReviewedIdentitySplitEditor } from '../src/components/ReviewedIdentitySplitEditor.tsx';
import { ApiRequestError } from '../src/lib/apiErrors.ts';
import type { ConcurrentLaneRefinement, Match, MixedPlayerCase, MixedPlayerFocusedCaseResponse, MixedPlayersReviewQueue, ReviewedCorrectionContext, ReviewWorkflow } from '../src/types.ts';

const normalActionCapabilities = {
  assign_roster_player: { allowed: true, requires_player_id: true },
  assign_existing_slot: { allowed: true, requires_slot_id: true },
  assign_team: { allowed: true, requires_team_label: true },
  create_new_stable_player: { allowed: true, requires_team_label: true },
  referee: { allowed: true },
  false_detection: { allowed: true },
  team_unknown: { allowed: true },
  unresolved: { allowed: true },
} as const;

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
    temporal_topology: {
      kind: 'serial',
      simple_split_allowed: true,
      tracklet_count: 1,
      max_concurrent_tracklets: 1,
      overlap_ranges: [],
      tracklets: [{
        tracklet_id: `track-${caseId}`,
        frame_start: observationCount,
        frame_end: observationCount + 10,
        observation_count: observationCount,
      }],
    },
    blocking: true,
    scope_status: 'blocking',
    action_capabilities: normalActionCapabilities,
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

function splittableMixedCase(caseId: string): MixedPlayerCase {
  const reviewCase = mixedCase(caseId, 2);
  return {
    ...reviewCase,
    frame_start: 10,
    frame_end: 20,
    temporal_evidence: {
      status: 'ready',
      anchor_crops: [10, 20].map((frame) => ({
        anchor_crop_id: `crop-${caseId}-${frame}`,
        artifact: `${caseId}-${frame}.jpg`,
        frame,
        time_sec: frame,
      })),
    },
  };
}

function concurrentMixedCase(caseId: string): MixedPlayerCase {
  const reviewCase = mixedCase(caseId, 4);
  return {
    ...reviewCase,
    frame_start: 10,
    frame_end: 30,
    source_tracklet_ids: ['track-A', 'track-B', 'track-C'],
    temporal_topology: {
      kind: 'concurrent',
      simple_split_allowed: false,
      tracklet_count: 3,
      max_concurrent_tracklets: 2,
      overlap_ranges: [{ frame_start: 15, frame_end: 20, tracklet_ids: ['track-A', 'track-B'] }],
      tracklets: [
        { tracklet_id: 'track-A', frame_start: 10, frame_end: 20, observation_count: 2 },
        { tracklet_id: 'track-B', frame_start: 15, frame_end: 20, observation_count: 1 },
        { tracklet_id: 'track-C', frame_start: 21, frame_end: 30, observation_count: 1 },
      ],
    },
    temporal_evidence: {
      status: 'ready',
      anchor_crops: [
        { anchor_crop_id: 'crop-A', artifact: 'A.jpg', frame: 15, time_sec: 15, tracklet_id: 'track-A' },
        { anchor_crop_id: 'crop-B', artifact: 'B.jpg', frame: 17, time_sec: 17, tracklet_id: 'track-B' },
        { anchor_crop_id: 'crop-C', artifact: 'C.jpg', frame: 25, time_sec: 25, tracklet_id: 'track-C' },
      ],
    },
    concurrent_resolution: {
      status: 'unresolved',
      parent_case_id: caseId,
      parent_source_digest: `digest-${caseId}`,
      lanes: [
        {
          lane_id: 'lane-A',
          tracklet_id: 'track-A',
          source_ownership_digest: 'lane-digest-A',
          frame_start: 10,
          frame_end: 20,
          observation_count: 2,
          split_allowed: true,
          overlap_lane_ids: ['lane-B'],
          evidence: {
            status: 'ready',
            anchor_crops: [
              { anchor_crop_id: 'lane-A-10', artifact: 'A-10.jpg', frame: 10, time_sec: 10, tracklet_id: 'track-A' },
              { anchor_crop_id: 'lane-A-20', artifact: 'A-20.jpg', frame: 20, time_sec: 20, tracklet_id: 'track-A' },
            ],
          },
        },
        {
          lane_id: 'lane-B',
          tracklet_id: 'track-B',
          source_ownership_digest: 'lane-digest-B',
          frame_start: 15,
          frame_end: 20,
          observation_count: 2,
          split_allowed: true,
          overlap_lane_ids: ['lane-A'],
          evidence: {
            status: 'ready',
            anchor_crops: [
              { anchor_crop_id: 'lane-B-15', artifact: 'B-15.jpg', frame: 15, time_sec: 15, tracklet_id: 'track-B' },
              { anchor_crop_id: 'lane-B-20', artifact: 'B-20.jpg', frame: 20, time_sec: 20, tracklet_id: 'track-B' },
            ],
          },
        },
        {
          lane_id: 'lane-C',
          tracklet_id: 'track-C',
          source_ownership_digest: 'lane-digest-C',
          frame_start: 21,
          frame_end: 30,
          observation_count: 2,
          split_allowed: true,
          overlap_lane_ids: [],
          evidence: {
            status: 'ready',
            anchor_crops: [
              { anchor_crop_id: 'lane-C-21', artifact: 'C-21.jpg', frame: 21, time_sec: 21, tracklet_id: 'track-C' },
              { anchor_crop_id: 'lane-C-30', artifact: 'C-30.jpg', frame: 30, time_sec: 30, tracklet_id: 'track-C' },
            ],
          },
        },
      ],
    },
  };
}

function laneRefinement(caseId: string): ConcurrentLaneRefinement {
  return {
    schema_version: '1.0.0',
    mode: 'reviewed_identity_concurrent_lane_refinement',
    match_id: match.id,
    candidate_subject_id: `subject-${caseId}`,
    parent_case_id: caseId,
    parent_source_digest: `digest-${caseId}`,
    lane_id: 'lane-A',
    lane_source_digest: 'lane-digest-A',
    after_frame: 100,
    before_frame: 160,
    boundary_crops: {
      after: { anchor_crop_id: 'overview-100', artifact: 'overview-100.jpg', frame: 100, time_sec: 100, tracklet_id: 'track-A' },
      before: { anchor_crop_id: 'overview-160', artifact: 'overview-160.jpg', frame: 160, time_sec: 160, tracklet_id: 'track-A' },
    },
    anchor_crops: [110, 120, 130, 140, 150, 160].map((frame) => ({
      anchor_crop_id: `refined-${frame}`,
      artifact: `refined-${frame}.jpg`,
      frame,
      time_sec: frame,
      tracklet_id: 'track-A',
    })),
  };
}

function workflowWithBlocking(normalBlocking: number, mixedBlocking: number): ReviewWorkflow {
  return {
    ...workflow,
    issues: {
      ...workflow.issues,
      blocking: normalBlocking + mixedBlocking,
      normal_blocking: normalBlocking,
      mixed_blocking: mixedBlocking,
    },
  };
}

async function submitValidStructuralSplit(view: ReturnType<typeof render>) {
  await waitFor(() => assert.ok(view.getByText('2 wykrytych obserwacji')));
  fireEvent.click(view.getByRole('button', { name: 'Podziel tutaj' }));
  fireEvent.click(view.getByText('Inne przypisanie'));
  fireEvent.click(view.getByRole('button', { name: 'Corgi — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Verisk — zawodnik nieznany' }));
  const save = view.getByRole('button', { name: 'Zapisz podział + następny' });
  await waitFor(() => assert.equal(save.hasAttribute('disabled'), false), { timeout: 2_000 });
  await act(async () => { fireEvent.click(save); });
}

test('concurrent Mixed case resolves every exact lane and saves once atomically', async () => {
  const concurrent = concurrentMixedCase('M-concurrent');
  const resolutions: string[] = [];
  const lanePayloads: unknown[] = [];
  let reprojects = 0;
  const view = renderPanel({
    getQueue: async () => queue([concurrent]),
    saveResolution: async (_matchId, payload) => {
      resolutions.push(payload.resolution);
      lanePayloads.push(payload.lane_resolutions);
      return { saved_case: concurrent, semantic_decision_digest: 'lanes', recompute_deferred: true };
    },
    reprojectWorkflow: async () => { reprojects += 1; return workflowWithBlocking(0, 0); },
  });

  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
  assert.ok(view.getByRole('button', { name: /Ścieżka 1/ }));
  assert.ok(view.getByRole('button', { name: /Ścieżka 2/ }));
  assert.ok(view.getByRole('button', { name: /Ścieżka 3/ }));
  assert.equal(view.queryByText('track-A'), null);
  assert.equal(view.queryByRole('button', { name: 'Podziel tutaj' }), null);
  assert.equal(view.queryByRole('button', { name: 'Doprecyzuj' }), null);
  assert.equal(view.queryByRole('button', { name: 'Zapisz podział + następny' }), null);
  const save = view.getByRole('button', { name: 'Zapisz przypisania + następny' });
  assert.equal(save.hasAttribute('disabled'), true);

  fireEvent.click(view.getByText('Inne przypisanie'));
  fireEvent.click(view.getByRole('button', { name: 'Corgi — zawodnik nieznany' }));
  await waitFor(() => assert.ok(view.getByText('1 z 3 ścieżek przypisane')));
  fireEvent.click(view.getByRole('button', { name: 'Verisk — zawodnik nieznany' }));
  await waitFor(() => assert.ok(view.getByText('2 z 3 ścieżek przypisane')));
  fireEvent.click(view.getByRole('button', { name: 'Nie wiem' }));
  await waitFor(() => assert.equal(save.hasAttribute('disabled'), false), { timeout: 2_000 });
  await act(async () => { fireEvent.click(save); });

  assert.deepEqual(resolutions, ['concurrent_lanes']);
  assert.equal((lanePayloads[0] as unknown[]).length, 3);
  assert.equal(reprojects, 1);
});

test('production correction-context response shape activates the exact lane resolver', () => {
  const concurrent = concurrentMixedCase('M-inline');
  const correctionContextResponse = {
    candidate_subject_id: concurrent.candidate_subject_id,
    review_target_id: null,
    scope_kind: 'whole_subject',
    team_label: 'A',
    source_team_label: 'A',
    effective_team_label: 'A',
    available_team_labels: ['A', 'B'],
    tracklet_ids: concurrent.source_tracklet_ids,
    review_card_key: null,
    roster_options: [],
    slot_options: [],
    current_decision: null,
    semantic_decision_digest: 'semantic',
    source_ownership_digest: concurrent.source_subject_digest,
    frame_start: concurrent.frame_start,
    frame_end: concurrent.frame_end,
    detected_observation_count: concurrent.observation_count,
    temporal_topology: concurrent.temporal_topology,
    concurrent_resolution: concurrent.concurrent_resolution,
    historical_concurrent_repair: false,
    visual_evidence: concurrent.temporal_evidence,
    source_evidence_kind: 'identity_continuity',
    temporal_split: null,
    action_capabilities: normalActionCapabilities,
    scope_copy: 'Korekta obejmuje cały pokazany fragment.',
    review_state_version: 42,
    server_timing: {
      review_hot_state_ms: 1,
      review_context_ms: 1,
      total_ms: 2,
    },
  } satisfies ReviewedCorrectionContext & { server_timing: Record<string, number> };
  const view = render(React.createElement(ReviewedIdentitySplitEditor, {
    matchId: match.id,
    context: correctionContextResponse,
    teams: match.teams,
    onCancel: () => undefined,
    onSaved: () => assert.fail('concurrent case must not report a split save'),
  }));

  assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' }));
  assert.equal(view.queryByRole('button', { name: 'Zapisz podział + następny' }), null);
  assert.equal(view.queryByRole('button', { name: 'Doprecyzuj' }), null);
  assert.ok(view.getByRole('button', { name: 'Nie da się bezpiecznie rozwiązać tego przypadku' }));
  assert.ok(view.getByRole('button', { name: 'Wróć bez zapisu' }));
});

test('canonical child opens its historical concurrent parent only after an explicit repair click', async () => {
  const parent = concurrentMixedCase('legacy-material-parent');
  const childContext = {
    candidate_subject_id: 'continuity:A12:100-599',
    review_target_id: 'review-mixed-segment:v1:child',
    scope_kind: 'canonical_segment',
    team_label: 'A',
    source_team_label: 'A',
    effective_team_label: 'A',
    available_team_labels: ['A'],
    tracklet_ids: ['material-tracklet'],
    review_card_key: null,
    roster_options: [],
    slot_options: [],
    current_decision: null,
    semantic_decision_digest: 'semantic',
    source_ownership_digest: 'child-digest',
    frame_start: 100,
    frame_end: 349,
    detected_observation_count: 250,
    temporal_topology: { ...parent.temporal_topology, kind: 'serial', simple_split_allowed: true, max_concurrent_tracklets: 1, overlap_ranges: [] },
    concurrent_resolution: null,
    historical_concurrent_repair: false,
    historical_parent_repair: { available: true, case_id: parent.case_id },
    visual_evidence: parent.temporal_evidence,
    source_evidence_kind: 'identity_continuity',
    temporal_split: null,
    action_capabilities: normalActionCapabilities,
    scope_copy: 'Korekta obejmuje dokładnie pokazany fragment.',
    review_state_version: 42,
  } satisfies ReviewedCorrectionContext;
  const parentContext = {
    ...childContext,
    candidate_subject_id: parent.candidate_subject_id,
    review_target_id: null,
    scope_kind: 'material_continuity',
    available_team_labels: ['A', 'B'],
    tracklet_ids: parent.source_tracklet_ids,
    source_ownership_digest: parent.source_subject_digest,
    frame_start: parent.frame_start,
    frame_end: parent.frame_end,
    detected_observation_count: parent.observation_count,
    temporal_topology: parent.temporal_topology,
    concurrent_resolution: parent.concurrent_resolution,
    historical_concurrent_repair: true,
    historical_parent_repair: null,
    visual_evidence: parent.temporal_evidence,
    temporal_split: {
      resolution_status: 'resolved',
      split_after_frames: [20],
      split_semantic_digest: 'old-split',
      segment_assignments: [
        { action: 'assign_team', team_label: 'A' },
        { action: 'assign_team', team_label: 'B' },
      ],
    },
  } satisfies ReviewedCorrectionContext;
  const entity = {
    frame: 100,
    time_sec: 10,
    tracklet_id: 'material-tracklet',
    candidate_subject_id: childContext.candidate_subject_id,
    candidate_subject_ids: [childContext.candidate_subject_id],
    team_label: 'A',
    stable_anonymous_slot_id: null,
    canonical_player_id: null,
    player_name: null,
    display_label: 'Nieznany zawodnik',
    identity_status: 'unresolved',
    identity_source: null,
    fallback_label: 'Nieznany',
    requires_review: true,
    hard_blockers: [],
    conflicts: [],
    detected_evidence_count: 250,
    frame_start: 100,
    frame_end: 349,
    review_target_id: childContext.review_target_id,
    scope_kind: 'canonical_segment',
  } as const;
  const originalFetch = globalThis.fetch;
  const requested: string[] = [];
  globalThis.fetch = async (input) => {
    const url = String(input);
    requested.push(url);
    if (url.includes('/corrections/historical-split/')) {
      return new Response(JSON.stringify(parentContext), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    if (url.includes('/reviewed-identity/corrections/context')) {
      return new Response(JSON.stringify(childContext), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    throw new Error(`unexpected request ${url}`);
  };
  try {
    const view = render(React.createElement(ReviewedIdentityCorrectionForm, {
      matchId: match.id,
      entity,
      teams: match.teams,
      onCancel: () => undefined,
      onSaved: () => assert.fail('opening a historical repair must not save'),
    }));
    await waitFor(() => assert.ok(view.getByRole('button', { name: 'Napraw równoległe przypisanie' })));
    assert.equal(requested.filter((url) => url.includes('/historical-split/')).length, 0);
    fireEvent.click(view.getByRole('button', { name: 'Napraw równoległe przypisanie' }));
    await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
    assert.ok(view.getByText('Historyczny podział czasowy nie jest już uznawany za bezpieczny.'));
    assert.equal(requested.filter((url) => url.includes('/historical-split/')).length, 1);
    assert.equal(requested.filter((url) => url.includes('/corrections')).length, 2);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ordinary canonical child never exposes a historical repair action without the exact parent link', async () => {
  const context = {
    candidate_subject_id: 'ordinary-child',
    review_target_id: 'review-mixed-segment:v1:ordinary',
    scope_kind: 'canonical_segment',
    team_label: 'A',
    source_team_label: 'A',
    effective_team_label: 'A',
    available_team_labels: ['A'],
    tracklet_ids: ['tracklet-ordinary'],
    review_card_key: null,
    roster_options: [],
    slot_options: [],
    current_decision: null,
    semantic_decision_digest: 'semantic',
    source_ownership_digest: 'ordinary-digest',
    frame_start: 10,
    frame_end: 20,
    detected_observation_count: 2,
    temporal_topology: {
      kind: 'serial',
      simple_split_allowed: true,
      tracklet_count: 1,
      max_concurrent_tracklets: 1,
      overlap_ranges: [],
      tracklets: [{ tracklet_id: 'tracklet-ordinary', frame_start: 10, frame_end: 20, observation_count: 2 }],
    },
    concurrent_resolution: null,
    historical_concurrent_repair: false,
    historical_parent_repair: null,
    visual_evidence: { status: 'ready', anchor_crops: [] },
    source_evidence_kind: 'identity_continuity',
    temporal_split: null,
    action_capabilities: normalActionCapabilities,
    scope_copy: 'Korekta obejmuje dokładnie pokazany fragment.',
    review_state_version: 42,
  } satisfies ReviewedCorrectionContext;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify(context), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });
  try {
    const view = render(React.createElement(ReviewedIdentityCorrectionForm, {
      matchId: match.id,
      entity: {
        frame: 10,
        time_sec: 1,
        tracklet_id: 'tracklet-ordinary',
        candidate_subject_id: context.candidate_subject_id,
        candidate_subject_ids: [context.candidate_subject_id],
        team_label: 'A',
        stable_anonymous_slot_id: null,
        canonical_player_id: null,
        player_name: null,
        display_label: 'Nieznany zawodnik',
        identity_status: 'unresolved',
        identity_source: null,
        fallback_label: 'Nieznany',
        requires_review: true,
        hard_blockers: [],
        conflicts: [],
        detected_evidence_count: 2,
        frame_start: 10,
        frame_end: 20,
        review_target_id: context.review_target_id,
        scope_kind: 'canonical_segment',
      },
      teams: match.teams,
      onCancel: () => undefined,
      onSaved: () => undefined,
    }));
    await waitFor(() => assert.ok(view.getByRole('radiogroup', { name: 'Rodzaj poprawki' })));
    assert.equal(view.queryByRole('button', { name: 'Napraw równoległe przypisanie' }), null);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('correction editor discards stale lane refinement drafts and refreshes its exact context once', async () => {
  const concurrent = concurrentMixedCase('M-inline-refinement-stale');
  const lanes = concurrent.concurrent_resolution!.lanes.map((lane, index) => ({
    ...lane,
    observation_count: index === 0 ? 12 : lane.observation_count,
  }));
  const context = {
    candidate_subject_id: concurrent.candidate_subject_id,
    scope_kind: 'whole_subject',
    team_label: 'A',
    source_team_label: 'A',
    effective_team_label: 'A',
    available_team_labels: ['A', 'B'],
    tracklet_ids: concurrent.source_tracklet_ids,
    review_card_key: null,
    roster_options: [],
    slot_options: [],
    current_decision: null,
    semantic_decision_digest: 'semantic',
    source_ownership_digest: concurrent.source_subject_digest,
    frame_start: concurrent.frame_start,
    frame_end: concurrent.frame_end,
    detected_observation_count: concurrent.observation_count,
    temporal_topology: concurrent.temporal_topology,
    concurrent_resolution: { ...concurrent.concurrent_resolution!, lanes },
    visual_evidence: concurrent.temporal_evidence,
    action_capabilities: normalActionCapabilities,
  } as ReviewedCorrectionContext;
  const fresh = {
    ...context,
    concurrent_resolution: {
      ...context.concurrent_resolution!,
      lanes: lanes.map((lane) => ({ ...lane, source_ownership_digest: `${lane.source_ownership_digest}-fresh` })),
    },
  };
  const originalFetch = globalThis.fetch;
  const requested: string[] = [];
  globalThis.fetch = async (input) => {
    const url = String(input);
    requested.push(url);
    if (url.includes('/concurrent-lanes/refine')) {
      return new Response(JSON.stringify({ detail: { code: 'concurrent_lane_source_stale', message: 'stale' } }), {
        status: 409,
        headers: { 'content-type': 'application/json' },
      });
    }
    if (url.includes('/reviewed-identity/corrections/context')) {
      return new Response(JSON.stringify(fresh), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    throw new Error(`unexpected request ${url}`);
  };
  try {
    const view = render(React.createElement(ReviewedIdentitySplitEditor, {
      matchId: match.id,
      context,
      teams: match.teams,
      onCancel: () => undefined,
      onSaved: () => assert.fail('stale refinement must not save'),
    }));
    fireEvent.click(view.getByRole('button', { name: 'Ta ścieżka zawiera więcej niż jednego zawodnika' }));
    fireEvent.click(view.getByRole('button', { name: 'Doprecyzuj' }));
    await waitFor(() => assert.ok(view.getByText(/Układ ścieżek został zaktualizowany/)));
    assert.equal(requested.filter((url) => url.includes('/concurrent-lanes/refine')).length, 1);
    assert.equal(requested.filter((url) => url.includes('/reviewed-identity/corrections/context')).length, 1);
    assert.ok(view.getByText('0 z 3 ścieżek przypisane'));
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('lane-local temporal split shows and edits only the selected lane evidence', async () => {
  const concurrent = concurrentMixedCase('M-lane-split');
  const view = renderPanel({ getQueue: async () => queue([concurrent]) });

  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
  fireEvent.click(view.getByRole('button', { name: 'Ta ścieżka zawiera więcej niż jednego zawodnika' }));

  assert.ok(view.getByRole('heading', { name: 'Podziel tylko tę ścieżkę' }));
  const laneImages = view.getAllByAltText('Podgląd wyłącznie wybranej ścieżki') as HTMLImageElement[];
  assert.equal(laneImages.length, 2);
  assert.ok(laneImages.every((image) => image.src.includes('/A-')));
  assert.equal(view.queryByAltText('Ścieżka 2, 00:15.0'), null);

  fireEvent.click(view.getByRole('button', { name: 'Podziel tutaj' }));
  fireEvent.click(view.getByText('Inne przypisanie'));
  fireEvent.click(view.getByRole('button', { name: 'Corgi — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Verisk — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Wróć do ścieżek' }));

  assert.equal(view.getAllByText('Podzielono na 2 fragmenty').length, 2);
  assert.ok(view.getByText('1 z 3 ścieżek przypisane'));
});

test('lane refinement keeps the leading after-preview boundary instead of shifting it to the first crop', async () => {
  const concurrent = concurrentMixedCase('M-lane-leading-boundary');
  const laneA = concurrent.concurrent_resolution!.lanes[0];
  const configured = {
    ...concurrent,
    concurrent_resolution: {
      ...concurrent.concurrent_resolution!,
      lanes: [{
        ...laneA,
        frame_start: 100,
        frame_end: 160,
        observation_count: 12,
        split_allowed: true,
        evidence: {
          ...laneA.evidence,
          anchor_crops: [100, 160].map((frame) => ({
            anchor_crop_id: `overview-${frame}`,
            artifact: `overview-${frame}.jpg`,
            frame,
            time_sec: frame,
            tracklet_id: 'track-A',
          })),
        },
      }, ...concurrent.concurrent_resolution!.lanes.slice(1)],
    },
  };
  const payloads: unknown[] = [];
  const view = renderPanel({
    getQueue: async () => queue([configured]),
    getLaneRefinement: async () => laneRefinement('M-lane-leading-boundary'),
    saveResolution: async (_matchId, payload) => {
      payloads.push(payload.lane_resolutions);
      return { saved_case: configured, semantic_decision_digest: 'leading', recompute_deferred: true };
    },
    reprojectWorkflow: async () => workflowWithBlocking(0, 0),
  });

  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
  fireEvent.click(view.getByRole('button', { name: 'Ta ścieżka zawiera więcej niż jednego zawodnika' }));
  fireEvent.click(view.getByRole('button', { name: 'Doprecyzuj' }));
  await waitFor(() => assert.ok(view.getByRole('button', { name: 'Podziel zaraz po poprzednim podglądzie' })));
  assert.ok(view.getByAltText('Lewy widok graniczny — podział następuje po tym widoku'));
  assert.ok(view.getByAltText('Prawy widok graniczny — podział następuje przed tym widokiem'));
  fireEvent.click(view.getByRole('button', { name: 'Podziel zaraz po poprzednim podglądzie' }));
  fireEvent.click(view.getByText('Inne przypisanie'));
  fireEvent.click(view.getByRole('button', { name: 'Corgi — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Verisk — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Wróć do ścieżek' }));
  fireEvent.click(view.getByRole('button', { name: /Ścieżka 2/ }));
  fireEvent.click(view.getByText('Inne przypisanie'));
  fireEvent.click(view.getByRole('button', { name: 'Corgi — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Nie wiem' }));
  const save = view.getByRole('button', { name: 'Zapisz przypisania + następny' });
  await waitFor(() => assert.equal(save.hasAttribute('disabled'), false));
  await act(async () => { fireEvent.click(save); });

  const laneResolution = (payloads[0] as Array<Record<string, unknown>>).find((value) => value.lane_id === 'lane-A');
  assert.deepEqual(laneResolution?.split_after_frames, [100]);
});

test('Mixed assignment controls hide material-continuity forbidden advanced actions', async () => {
  const concurrent = {
    ...concurrentMixedCase('M-material-capabilities'),
    action_capabilities: {
      ...normalActionCapabilities,
      assign_existing_slot: { allowed: false },
      create_new_stable_player: { allowed: false },
    },
  };
  const view = renderPanel({ getQueue: async () => queue([concurrent]) });
  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
  assert.ok(view.getByLabelText('Zawodnik z kadry'));
  assert.equal(view.queryByLabelText('Ten sam co wcześniej'), null);
  fireEvent.click(view.getByText('Inne przypisanie'));
  assert.equal(view.queryByText(/Nowy zawodnik/), null);
  assert.ok(view.getByRole('button', { name: 'Corgi — zawodnik nieznany' }));
  assert.ok(view.getByRole('button', { name: 'Nie wiem' }));
});

test('Mixed assignment controls retain advanced actions when the server allows them', async () => {
  const view = renderPanel({ getQueue: async () => queue([concurrentMixedCase('M-normal-capabilities')]) });
  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
  assert.ok(view.getByLabelText('Ten sam co wcześniej'));
  fireEvent.click(view.getByText('Inne przypisanie'));
  assert.ok(view.getByRole('button', { name: 'Nowy zawodnik (Corgi)' }));
});

test('only server-splittable lanes offer the local split workflow', async () => {
  const concurrent = concurrentMixedCase('M-lane-splittability');
  const lanes = concurrent.concurrent_resolution!.lanes.map((lane, index) => ({
    ...lane,
    observation_count: index === 0 ? 1 : lane.observation_count,
    split_allowed: index !== 0,
  }));
  const view = renderPanel({ getQueue: async () => queue([{
    ...concurrent,
    concurrent_resolution: { ...concurrent.concurrent_resolution!, lanes },
  }]) });
  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
  assert.equal(view.queryByRole('button', { name: 'Ta ścieżka zawiera więcej niż jednego zawodnika' }), null);
  fireEvent.click(view.getByRole('button', { name: /Ścieżka 2/ }));
  assert.ok(view.getByRole('button', { name: 'Ta ścieżka zawiera więcej niż jednego zawodnika' }));
});

test('stale lane refinement refreshes the exact case once without save or reproject', async () => {
  const stale = concurrentMixedCase('M-refinement-stale');
  const fresh = {
    ...stale,
    concurrent_resolution: {
      ...stale.concurrent_resolution!,
      lanes: stale.concurrent_resolution!.lanes.map((lane, index) => ({
        ...lane,
        observation_count: index === 0 ? 12 : lane.observation_count,
        source_ownership_digest: `${lane.source_ownership_digest}-fresh`,
      })),
    },
  };
  let focusedReads = 0;
  let saves = 0;
  let reprojects = 0;
  const view = renderPanel({
    getQueue: async () => queue([{ ...stale, concurrent_resolution: {
      ...stale.concurrent_resolution!,
      lanes: stale.concurrent_resolution!.lanes.map((lane, index) => ({ ...lane, observation_count: index === 0 ? 12 : lane.observation_count })),
    } }]),
    getLaneRefinement: async () => { throw new ApiRequestError(409, 'stale lane', 'concurrent_lane_source_stale'); },
    getFocusedCase: async () => {
      focusedReads += 1;
      return focusedResponse('M-refinement-stale', 'current_blocking', fresh);
    },
    saveResolution: async () => { saves += 1; throw new Error('must not save'); },
    reprojectWorkflow: async () => { reprojects += 1; return workflow; },
  });
  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
  fireEvent.click(view.getByRole('button', { name: 'Ta ścieżka zawiera więcej niż jednego zawodnika' }));
  fireEvent.click(view.getByRole('button', { name: 'Doprecyzuj' }));
  await waitFor(() => assert.ok(view.getByText(/Układ ścieżek został zaktualizowany/)));
  assert.equal(focusedReads, 1);
  assert.equal(saves, 0);
  assert.equal(reprojects, 0);
  assert.ok(view.getByText('0 z 3 ścieżek przypisane'));
});

test('failed stale lane-refinement refresh remains fail-closed', async () => {
  const concurrent = concurrentMixedCase('M-refinement-stale-fail');
  const view = renderPanel({
    getQueue: async () => queue([{ ...concurrent, concurrent_resolution: {
      ...concurrent.concurrent_resolution!,
      lanes: concurrent.concurrent_resolution!.lanes.map((lane, index) => ({ ...lane, observation_count: index === 0 ? 12 : lane.observation_count })),
    } }]),
    getLaneRefinement: async () => { throw new ApiRequestError(409, 'stale lane', 'concurrent_lane_source_stale'); },
    getFocusedCase: async () => { throw new Error('offline'); },
    saveResolution: async () => { throw new Error('must not save'); },
    reprojectWorkflow: async () => { throw new Error('must not reproject'); },
  });
  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
  fireEvent.click(view.getByRole('button', { name: 'Ta ścieżka zawiera więcej niż jednego zawodnika' }));
  fireEvent.click(view.getByRole('button', { name: 'Doprecyzuj' }));
  await waitFor(() => assert.ok(view.getByText(/Nie udało się pobrać aktualnych ścieżek/)));
  assert.equal(view.queryByRole('heading', { name: 'Przypisz równoległych zawodników' }), null);
  assert.equal(view.queryByRole('button', { name: 'Zapisz przypisania + następny' }), null);
});

test('serial-to-concurrent save race refreshes exact case without retry or fake success', async () => {
  const serial = splittableMixedCase('M-race');
  const concurrent = concurrentMixedCase('M-race');
  let saves = 0;
  let focusedReads = 0;
  let reprojects = 0;
  const view = renderPanel({
    getQueue: async () => queue([serial]),
    saveResolution: async () => {
      saves += 1;
      throw new ApiRequestError(
        409,
        'temporal_split_not_separable',
        'temporal_split_not_separable',
      );
    },
    getFocusedCase: async () => {
      focusedReads += 1;
      return focusedResponse('M-race', 'current_blocking', concurrent);
    },
    reprojectWorkflow: async () => {
      reprojects += 1;
      return workflow;
    },
  });

  await submitValidStructuralSplit(view);
  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));

  assert.equal(saves, 1);
  assert.equal(focusedReads, 1);
  assert.equal(reprojects, 0);
  assert.equal(view.queryByRole('button', { name: 'Zapisz podział + następny' }), null);
  assert.ok(view.getByText(/Pokazano aktualny przypadek/));
});

test('stale concurrent lane save exact-refreshes once and never retries the POST automatically', async () => {
  const stale = concurrentMixedCase('M-lane-stale');
  const fresh = {
    ...concurrentMixedCase('M-lane-stale'),
    source_subject_digest: 'digest-M-lane-fresh',
    concurrent_resolution: {
      ...concurrentMixedCase('M-lane-stale').concurrent_resolution!,
      parent_source_digest: 'digest-M-lane-fresh',
      lanes: concurrentMixedCase('M-lane-stale').concurrent_resolution!.lanes.map((lane) => ({
        ...lane,
        source_ownership_digest: `${lane.source_ownership_digest}-fresh`,
        current_resolution: null,
      })),
    },
  };
  let saves = 0;
  let focusedReads = 0;
  let reprojects = 0;
  const view = renderPanel({
    getQueue: async () => queue([stale]),
    getFocusedCase: async () => {
      focusedReads += 1;
      return focusedResponse('M-lane-stale', 'current_blocking', fresh);
    },
    saveResolution: async () => {
      saves += 1;
      if (saves === 1) {
        throw new ApiRequestError(409, 'lane set changed', 'concurrent_lane_set_stale');
      }
      return { saved_case: fresh, semantic_decision_digest: 'fresh-save', recompute_deferred: true };
    },
    reprojectWorkflow: async () => { reprojects += 1; return workflowWithBlocking(0, 0); },
  });

  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
  fireEvent.click(view.getByText('Inne przypisanie'));
  fireEvent.click(view.getByRole('button', { name: 'Corgi — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Verisk — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Nie wiem' }));
  await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Zapisz przypisania + następny' })); });

  await waitFor(() => assert.ok(view.getByText(/Układ ścieżek został zaktualizowany/)));
  assert.equal(saves, 1);
  assert.equal(focusedReads, 1);
  assert.equal(reprojects, 0);
  assert.ok(view.getByText('0 z 3 ścieżek przypisane'));

  fireEvent.click(view.getByText('Inne przypisanie'));
  fireEvent.click(view.getByRole('button', { name: 'Corgi — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Verisk — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Nie wiem' }));
  await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Zapisz przypisania + następny' })); });
  await waitFor(() => assert.equal(reprojects, 1));
  assert.equal(saves, 2);
});

test('concurrent lane save ignores a repeated click while the atomic POST is in flight', async () => {
  const concurrent = concurrentMixedCase('M-lane-double-save');
  const pending = deferred<{ saved_case: MixedPlayerCase; semantic_decision_digest: string; recompute_deferred: true }>();
  let saves = 0;
  const view = renderPanel({
    getQueue: async () => queue([concurrent]),
    saveResolution: async () => {
      saves += 1;
      return pending.promise;
    },
    reprojectWorkflow: async () => workflowWithBlocking(0, 0),
  });

  await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Przypisz równoległych zawodników' })));
  fireEvent.click(view.getByText('Inne przypisanie'));
  fireEvent.click(view.getByRole('button', { name: 'Corgi — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Verisk — zawodnik nieznany' }));
  fireEvent.click(view.getByRole('button', { name: 'Nie wiem' }));
  const save = view.getByRole('button', { name: 'Zapisz przypisania + następny' });
  fireEvent.click(save);
  fireEvent.click(save);
  assert.equal(saves, 1);
  await act(async () => pending.resolve({
    saved_case: concurrent,
    semantic_decision_digest: 'one-save',
    recompute_deferred: true,
  }));
});

test('failed exact recovery after split rejection keeps stale serial case fail-closed', async () => {
  const serial = splittableMixedCase('M-save-recovery-fails');
  let saves = 0;
  let focusedReads = 0;
  let reprojects = 0;
  const view = renderPanel({
    getQueue: async () => queue([serial]),
    saveResolution: async () => {
      saves += 1;
      throw new ApiRequestError(409, 'topology changed', 'temporal_split_not_separable');
    },
    getFocusedCase: async () => {
      focusedReads += 1;
      throw new Error('focused read unavailable');
    },
    reprojectWorkflow: async () => {
      reprojects += 1;
      return workflow;
    },
  });

  await submitValidStructuralSplit(view);
  await waitFor(() => assert.ok(view.getByText(/Nie udało się odświeżyć aktualnego przypadku/)));

  assert.equal(saves, 1);
  assert.equal(focusedReads, 1);
  assert.equal(reprojects, 0);
  assert.equal(view.queryByRole('button', { name: 'Zapisz podział + następny' }), null);
  assert.equal(view.queryByRole('button', { name: 'Podziel tutaj' }), null);
  assert.equal(view.queryByRole('button', { name: 'Doprecyzuj' }), null);
  assert.ok(view.getByRole('button', { name: 'Nie ma prostego podziału czasowego' }));
});

test('failed exact recovery after refinement rejection keeps stale serial case fail-closed', async () => {
  const serial = {
    ...splittableMixedCase('M-refinement-recovery-fails'),
    observation_count: 13,
  };
  let refinements = 0;
  let focusedReads = 0;
  let saves = 0;
  let reprojects = 0;
  const view = renderPanel({
    getQueue: async () => queue([serial]),
    getBoundaryRefinement: async () => {
      refinements += 1;
      throw new ApiRequestError(409, 'topology changed', 'temporal_split_not_separable');
    },
    getFocusedCase: async () => {
      focusedReads += 1;
      throw new Error('focused read unavailable');
    },
    saveResolution: async () => {
      saves += 1;
      throw new Error('save must not run');
    },
    reprojectWorkflow: async () => {
      reprojects += 1;
      return workflow;
    },
  });

  await waitFor(() => assert.ok(view.getByText('13 wykrytych obserwacji')));
  await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Doprecyzuj' })); });
  await waitFor(() => assert.ok(view.getByText(/Nie udało się odświeżyć aktualnego przypadku/)));

  assert.equal(refinements, 1);
  assert.equal(focusedReads, 1);
  assert.equal(saves, 0);
  assert.equal(reprojects, 0);
  assert.equal(view.queryByRole('button', { name: 'Zapisz podział + następny' }), null);
  assert.equal(view.queryByRole('button', { name: 'Podziel tutaj' }), null);
  assert.equal(view.queryByRole('button', { name: 'Doprecyzuj' }), null);
});

test('topology rejection for one case resets after exact navigation to another case', async () => {
  const m1 = splittableMixedCase('M1-rejected');
  const m2 = {
    ...splittableMixedCase('M2-authoritative'),
    observation_count: 3,
  };
  const focusedIds: string[] = [];
  const view = renderPanel({
    getQueue: async () => queue([m1, m2]),
    saveResolution: async () => {
      throw new ApiRequestError(409, 'topology changed', 'temporal_split_not_separable');
    },
    getFocusedCase: async (_matchId, caseId) => {
      focusedIds.push(caseId);
      if (caseId === m1.case_id) throw new Error('focused read unavailable');
      return focusedResponse(m2.case_id, 'current_blocking', m2);
    },
  });

  await submitValidStructuralSplit(view);
  await waitFor(() => assert.ok(view.getByText(/Nie udało się odświeżyć aktualnego przypadku/)));
  assert.equal(view.queryByRole('button', { name: 'Podziel tutaj' }), null);

  fireEvent.click(view.getByRole('button', { name: 'Następny' }));
  await waitFor(() => assert.ok(view.getByText('3 wykrytych obserwacji')));

  assert.deepEqual(focusedIds, [m1.case_id, m2.case_id]);
  assert.ok(view.getByRole('button', { name: 'Podziel tutaj' }));
});

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
  fireEvent.click(view.getByText('Inne przypisanie'));
  fireEvent.click(view.getByRole('button', { name: 'Corgi — zawodnik nieznany' }));
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

test('successful structural retry that remains in Mixed completes resolve-now intent once', async () => {
  const m1 = splittableMixedCase('M1');
  const m2 = mixedCase('M2', 22);
  let saves = 0;
  let reprojects = 0;
  let fullQueueReads = 0;
  let resolveNowCompletions = 0;
  let requiredReturns = 0;
  const view = render(React.createElement(MixedPlayersReviewPanel, {
    match,
    workflow,
    focusCaseId: 'M1',
    entryMode: 'resolve_now',
    onWorkflowChanged: () => undefined,
    onResolveNowComplete: () => { resolveNowCompletions += 1; },
    onReturnToRequired: () => { requiredReturns += 1; },
    reviewApi: {
      getFocusedCase: async () => focusedResponse('M1', 'current_blocking', m1),
      saveResolution: async () => {
        saves += 1;
        return { saved_case: m1, semantic_decision_digest: 'saved-M1', recompute_deferred: true };
      },
      reprojectWorkflow: async () => {
        reprojects += 1;
        if (reprojects === 1) throw new Error('first reproject failed');
        return workflowWithBlocking(0, 2);
      },
      getQueue: async () => { fullQueueReads += 1; return queue([m2]); },
    },
  }));

  await submitValidStructuralSplit(view);
  await waitFor(() => assert.ok(view.getByText('Podział został zapisany, ale kolejka Review wymaga odświeżenia.')));
  assert.equal(resolveNowCompletions, 0);
  await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Spróbuj odświeżyć Review' })); });
  await waitFor(() => assert.ok(view.getByText('22 wykrytych obserwacji')));

  assert.equal(saves, 1);
  assert.equal(reprojects, 2);
  assert.equal(resolveNowCompletions, 1);
  assert.equal(requiredReturns, 0);
  assert.equal(fullQueueReads, 1);
});

test('successful structural retry returns resolve-now to Required without reloading Mixed', async () => {
  const m1 = splittableMixedCase('M1');
  let saves = 0;
  let reprojects = 0;
  let fullQueueReads = 0;
  let resolveNowCompletions = 0;
  let requiredReturns = 0;
  const view = render(React.createElement(MixedPlayersReviewPanel, {
    match,
    workflow,
    focusCaseId: 'M1',
    entryMode: 'resolve_now',
    onWorkflowChanged: () => undefined,
    onResolveNowComplete: () => { resolveNowCompletions += 1; },
    onReturnToRequired: () => { requiredReturns += 1; },
    reviewApi: {
      getFocusedCase: async () => focusedResponse('M1', 'current_blocking', m1),
      saveResolution: async () => {
        saves += 1;
        return { saved_case: m1, semantic_decision_digest: 'saved-M1', recompute_deferred: true };
      },
      reprojectWorkflow: async () => {
        reprojects += 1;
        if (reprojects === 1) throw new Error('first reproject failed');
        return workflowWithBlocking(3, 2);
      },
      getQueue: async () => { fullQueueReads += 1; return queue([]); },
    },
  }));

  await submitValidStructuralSplit(view);
  await waitFor(() => assert.ok(view.getByText('Podział został zapisany, ale kolejka Review wymaga odświeżenia.')));
  await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Spróbuj odświeżyć Review' })); });
  await waitFor(() => assert.equal(requiredReturns, 1));

  assert.equal(saves, 1);
  assert.equal(reprojects, 2);
  assert.equal(resolveNowCompletions, 0);
  assert.equal(fullQueueReads, 0);
});

test('successful structural retry to workflow completes resolve-now intent without replaying split', async () => {
  const m1 = splittableMixedCase('M1');
  let saves = 0;
  let reprojects = 0;
  let fullQueueReads = 0;
  let resolveNowCompletions = 0;
  let requiredReturns = 0;
  const view = render(React.createElement(MixedPlayersReviewPanel, {
    match,
    workflow,
    focusCaseId: 'M1',
    entryMode: 'resolve_now',
    onWorkflowChanged: () => undefined,
    onResolveNowComplete: () => { resolveNowCompletions += 1; },
    onReturnToRequired: () => { requiredReturns += 1; },
    reviewApi: {
      getFocusedCase: async () => focusedResponse('M1', 'current_blocking', m1),
      saveResolution: async () => {
        saves += 1;
        return { saved_case: m1, semantic_decision_digest: 'saved-M1', recompute_deferred: true };
      },
      reprojectWorkflow: async () => {
        reprojects += 1;
        if (reprojects === 1) throw new Error('first reproject failed');
        return workflowWithBlocking(0, 0);
      },
      getQueue: async () => { fullQueueReads += 1; return queue([]); },
    },
  }));

  await submitValidStructuralSplit(view);
  await waitFor(() => assert.ok(view.getByText('Podział został zapisany, ale kolejka Review wymaga odświeżenia.')));
  await act(async () => { fireEvent.click(view.getByRole('button', { name: 'Spróbuj odświeżyć Review' })); });
  await waitFor(() => assert.equal(resolveNowCompletions, 1));

  assert.equal(saves, 1);
  assert.equal(reprojects, 2);
  assert.equal(requiredReturns, 0);
  assert.equal(fullQueueReads, 0);
});

test('normal successful resolve-now reproject to workflow clears its one-shot intent', async () => {
  const m1 = splittableMixedCase('M1');
  let saves = 0;
  let reprojects = 0;
  let resolveNowCompletions = 0;
  const view = render(React.createElement(MixedPlayersReviewPanel, {
    match,
    workflow,
    focusCaseId: 'M1',
    entryMode: 'resolve_now',
    onWorkflowChanged: () => undefined,
    onResolveNowComplete: () => { resolveNowCompletions += 1; },
    reviewApi: {
      getFocusedCase: async () => focusedResponse('M1', 'current_blocking', m1),
      saveResolution: async () => {
        saves += 1;
        return { saved_case: m1, semantic_decision_digest: 'saved-M1', recompute_deferred: true };
      },
      reprojectWorkflow: async () => {
        reprojects += 1;
        return workflowWithBlocking(0, 0);
      },
    },
  }));

  await submitValidStructuralSplit(view);
  await waitFor(() => assert.equal(resolveNowCompletions, 1));

  assert.equal(saves, 1);
  assert.equal(reprojects, 1);
});
