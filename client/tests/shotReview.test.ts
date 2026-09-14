import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React from 'react';

import { RedesignedReportVideoMoments } from '../src/components/RedesignedReportVideoMoments.tsx';
import { ShotForm } from '../src/components/ShotForm.tsx';
import type { KeyMomentEditorState, PublicMatchReport, ShotReviewEditorState } from '../src/types.ts';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/matches/published-test/report?dev=1' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { act, cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');
const originalConfirm = window.confirm;

class MockYouTubePlayer {
  readonly seekCalls: Array<[number, boolean]> = [];
  currentTime = 77.5;
  constructor(readonly target: HTMLIFrameElement, private readonly options: { events?: { onReady?: (event: { target: MockYouTubePlayer }) => void } }) {}
  seekTo(seconds: number, allow: boolean) { this.seekCalls.push([seconds, allow]); }
  playVideo() {}
  getCurrentTime() { return this.currentTime; }
  destroy() {}
  ready() { this.options.events?.onReady?.({ target: this }); }
}

const report = {
  schema_version: 'public_match_report.v1', generated_at: '2026-09-13T00:00:00Z', id: 'published-test', source_match_id: 'source', report_type: 'public_match_report',
  match: { id: 'published-test', title: 'Corgi – Verisk', duration_sec: 300 },
  teams: [{ team_id: 'corgi', team_name: 'Corgi' }, { team_id: 'verisk', team_name: 'Verisk' }],
  players: [{ player_id: 'p-corgi-1', player_name: 'Roman', team_id: 'corgi' }, { player_id: 'p-verisk-1', player_name: 'Kuba', team_id: 'verisk' }],
  key_moments: { schema_version: 'key-moments.v1', moments: [{ moment_id: 'moment-1', time_sec: 40, headline: 'Akcja', origin: 'manual', public_category: 'other' }] },
} as PublicMatchReport;

const externalVideo = {
  group_id: 'group', status: 'current' as const,
  external_video: { provider: 'youtube' as const, video_id: 'AbCdEfGhI_1', source_url: 'https://www.youtube.com/watch?v=AbCdEfGhI_1', embed_url: 'https://www.youtube-nocookie.com/embed/AbCdEfGhI_1', linked_video: { generation_id: 'g', input_semantic_digest: 'input', output_semantic_digest: 'output', timeline_span_sec: 300 }, updated_at: 'now' },
};

function keyMomentState(): KeyMomentEditorState {
  return { key_moment_editor_allowed: true, revision: 'km-r1', moments: [{ moment_id: 'km-1', time_sec: 40, category: 'other', headline: 'Moment', origin: 'manual' }], suggestions: { status: 'ready', unreviewed_count: 0, candidates: [] } };
}

function shotState(suggestion: Partial<ShotReviewEditorState['unreviewed_suggestions'][number]> = {}): ShotReviewEditorState {
  const candidate = { candidate_id: 'candidate-1', logical_timestamp_sec: 100, suggested_team_name: 'Verisk', confidence: .74, ...suggestion };
  return {
    published_id: 'published-test', revision: 'shot-r1', has_editorial_sidecar: true,
    canonical_shots: [{ shot_id: 'shot-1', time_sec: 50, team_id: 'corgi', outcome: 'blocked', player_id: 'p-corgi-1', origin: 'manual', location_m: { x: 4, y: 5 }, location_source: 'ball' }],
    candidate_generation_digest: 'candidate-r1', candidate_count: 1, accepted_count: 0, rejected_count: 0, unreviewed_count: 1,
    unreviewed_suggestions: [candidate], cluster_count: 1, unreviewed_cluster_count: 1,
    unreviewed_suggestion_clusters: [{ cluster_id: 'cluster-1', source_match_id: 'source', review_start_time_sec: 99.5, review_end_time_sec: 100.5, member_count: 1, member_candidate_ids: ['candidate-1'], member_candidates: [candidate], preferred_candidate_id: 'candidate-1', preferred_candidate: candidate, status: 'unreviewed' }],
    suggestions: { status: 'ready' },
  };
}

function installPlayer(): MockYouTubePlayer[] {
  const players: MockYouTubePlayer[] = [];
  class Player extends MockYouTubePlayer {
    constructor(target: HTMLIFrameElement, options: ConstructorParameters<typeof MockYouTubePlayer>[1]) { super(target, options); players.push(this); }
  }
  window.YT = { Player };
  return players;
}

function renderReview(callbacks: Partial<React.ComponentProps<typeof RedesignedReportVideoMoments>> = {}, shots = shotState()) {
  const keyState = keyMomentState();
  return render(React.createElement(RedesignedReportVideoMoments, {
    report, externalVideo, editorState: keyState, shotReviewState: shots,
    onSaveEditor: async () => keyState, onAcceptSuggestion: async () => keyState, onRejectSuggestion: async () => keyState,
    onCreateShot: async () => shots, onEditShot: async () => shots, onDeleteShot: async () => shots,
    onAcceptShotSuggestionCluster: async () => shots, onRejectShotSuggestionCluster: async () => shots,
    ...callbacks,
  }));
}

afterEach(() => { cleanup(); window.YT = undefined; window.confirm = originalConfirm; });

test('Shot Review is dev-only while the public Key Moments panel remains unchanged', () => {
  const publicView = render(React.createElement(RedesignedReportVideoMoments, { report, externalVideo }));
  assert.equal(publicView.queryByRole('tab', { name: 'Strzały' }), null);
  assert.equal(publicView.queryByRole('button', { name: '+ Dodaj strzał' }), null);
});

test('domain switch preserves the one persistent YouTube player and shot rows seek it', async () => {
  const players = installPlayer();
  const view = renderReview();
  const iframe = view.container.querySelector('iframe');
  await waitFor(() => assert.equal(players.length, 1));
  await act(async () => { players[0].ready(); });
  fireEvent.click(view.getByRole('tab', { name: 'Strzały' }));
  assert.ok(view.getByText('Zaakceptowane strzały (1)'));
  assert.ok(view.getByText('Zablokowany'));
  assert.ok(view.getByText('Corgi · Roman'));
  fireEvent.click(view.getByRole('button', { name: 'Odtwórz' }));
  assert.deepEqual(players[0].seekCalls, [[50, true]]);
  assert.equal(view.container.querySelector('iframe'), iframe);
  assert.equal(players.length, 1);
});

test('prefilled suggested team accepts after the operator chooses only an outcome, then supports reject', async () => {
  const players = installPlayer();
  let accepted: unknown;
  let rejected: unknown;
  const view = renderReview({
    onAcceptShotSuggestionCluster: async (payload) => { accepted = payload; return shotState(); },
    onRejectShotSuggestionCluster: async (payload) => { rejected = payload; return shotState(); },
  });
  await waitFor(() => assert.equal(players.length, 1));
  await act(async () => { players[0].ready(); });
  fireEvent.click(view.getByRole('tab', { name: 'Strzały' }));
  fireEvent.click(view.getByRole('tab', { name: 'Sugestie 1' }));
  assert.ok(view.getByText('Potencjalne akcje (1)'));
  assert.ok(view.getByText('Verisk · 1:40.0 · 0.74'));
  fireEvent.click(view.getByRole('button', { name: 'Odtwórz' }));
  assert.deepEqual(players[0].seekCalls, [[99.5, true]]);
  fireEvent.click(view.getByRole('button', { name: 'Akceptuj' }));
  assert.ok(view.getByRole('heading', { name: 'Akceptuj sugerowany strzał' }));
  assert.equal((view.getByLabelText('Drużyna strzału') as HTMLSelectElement).value, 'verisk');
  fireEvent.click(view.getByRole('button', { name: 'Użyj aktualnego czasu filmu' }));
  assert.equal((view.getByLabelText('Czas strzału') as HTMLInputElement).value, '1:17.5');
  fireEvent.change(view.getByLabelText('Wynik strzału'), { target: { value: 'on_target' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz strzał' }));
  await waitFor(() => assert.ok(accepted));
  assert.deepEqual(accepted, { expected_revision: 'shot-r1', cluster_id: 'cluster-1', candidate_generation_digest: 'candidate-r1', shot: { time_sec: 77.5, team_id: 'verisk', outcome: 'on_target', player_id: null } });

  fireEvent.click(view.getByRole('tab', { name: 'Sugestie 1' }));
  fireEvent.click(view.getByRole('button', { name: 'Odrzuć' }));
  await waitFor(() => assert.ok(rejected));
  assert.deepEqual(rejected, { expected_revision: 'shot-r1', cluster_id: 'cluster-1', candidate_generation_digest: 'candidate-r1' });
});

test('unresolved suggested team still requires a team selection before acceptance', async () => {
  let accepted: unknown;
  const shots = shotState({ suggested_team_name: null, suggested_team_label: null });
  const view = renderReview({ onAcceptShotSuggestionCluster: async (payload) => { accepted = payload; return shots; } }, shots);
  fireEvent.click(view.getByRole('tab', { name: 'Strzały' }));
  fireEvent.click(view.getByRole('tab', { name: 'Sugestie 1' }));
  fireEvent.click(view.getByRole('button', { name: 'Akceptuj' }));
  assert.equal((view.getByLabelText('Drużyna strzału') as HTMLSelectElement).value, '');
  fireEvent.change(view.getByLabelText('Wynik strzału'), { target: { value: 'blocked' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz strzał' }));
  assert.match(view.getByText('Wybierz drużynę.').textContent || '', /Wybierz drużynę/);
  assert.equal(accepted, undefined);
  fireEvent.change(view.getByLabelText('Drużyna strzału'), { target: { value: 'corgi' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz strzał' }));
  await waitFor(() => assert.ok(accepted));
});

test('changing a prefilled suggested team clears an incompatible player', async () => {
  let saved: unknown;
  const view = render(React.createElement(ShotForm, {
    mode: 'accept', report,
    initial: { time_sec: 100, team_id: 'verisk', outcome: '', player_id: 'p-verisk-1', location_m: null, location_source: 'unavailable' },
    onCancel: () => {}, onSave: async (payload) => { saved = payload; },
  }));
  assert.equal((view.getByLabelText('Zawodnik strzału') as HTMLSelectElement).value, 'p-verisk-1');
  fireEvent.change(view.getByLabelText('Drużyna strzału'), { target: { value: 'corgi' } });
  assert.equal((view.getByLabelText('Zawodnik strzału') as HTMLSelectElement).value, '');
  fireEvent.change(view.getByLabelText('Wynik strzału'), { target: { value: 'goal' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz strzał' }));
  await waitFor(() => assert.deepEqual(saved, { time_sec: 100, team_id: 'corgi', outcome: 'goal', player_id: null }));
});

test('manual add uses current player time; editing omits read-only location_m and explicit pitch click sends override', async () => {
  const players = installPlayer();
  let created: unknown;
  let edited: unknown;
  const view = renderReview({
    onCreateShot: async (payload) => { created = payload; return shotState(); },
    onEditShot: async (_id, payload) => { edited = payload; return shotState(); },
  });
  await waitFor(() => assert.equal(players.length, 1));
  await act(async () => { players[0].ready(); });
  fireEvent.click(view.getByRole('tab', { name: 'Strzały' }));
  fireEvent.click(view.getByRole('button', { name: '+ Dodaj strzał' }));
  assert.equal((view.getByLabelText('Czas strzału') as HTMLInputElement).value, '1:17.5');
  fireEvent.change(view.getByLabelText('Drużyna strzału'), { target: { value: 'corgi' } });
  fireEvent.change(view.getByLabelText('Wynik strzału'), { target: { value: 'goal' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz strzał' }));
  await waitFor(() => assert.ok(created));
  assert.deepEqual(created, { expected_revision: 'shot-r1', shot: { time_sec: 77.5, team_id: 'corgi', outcome: 'goal', player_id: null } });

  fireEvent.click(view.getByRole('button', { name: 'Edytuj' }));
  fireEvent.change(view.getByLabelText('Wynik strzału'), { target: { value: 'off_target' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz strzał' }));
  await waitFor(() => assert.ok(edited));
  assert.deepEqual(edited, { expected_revision: 'shot-r1', shot: { time_sec: 50, team_id: 'corgi', outcome: 'off_target', player_id: 'p-corgi-1' } });

  fireEvent.click(view.getByRole('button', { name: 'Edytuj' }));
  fireEvent.click(view.getByRole('button', { name: 'Popraw pozycję ręcznie' }));
  const pitch = view.getByRole('img', { name: 'Wybór ręcznej pozycji strzału na boisku' });
  Object.defineProperty(pitch, 'getBoundingClientRect', { value: () => ({ left: 0, top: 0, width: 100, height: 160 }) });
  fireEvent.click(pitch, { clientX: 50, clientY: 80 });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz strzał' }));
  await waitFor(() => assert.equal((edited as { shot: { manual_location_override?: unknown } }).shot.manual_location_override != null, true));
  assert.deepEqual((edited as { shot: { manual_location_override?: unknown } }).shot.manual_location_override, { x: 15, y: 23.7 });
});

test('deleting a canonical shot calls the authoritative delete endpoint', async () => {
  let deleted: unknown;
  const view = renderReview({ onDeleteShot: async (_id, payload) => { deleted = payload; return shotState(); } });
  fireEvent.click(view.getByRole('tab', { name: 'Strzały' }));
  window.confirm = () => true;
  fireEvent.click(view.getByRole('button', { name: 'Usuń' }));
  await waitFor(() => assert.deepEqual(deleted, { expected_revision: 'shot-r1' }));
});
