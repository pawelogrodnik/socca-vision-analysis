import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React from 'react';

import { RedesignedReportVideoMoments } from '../src/components/RedesignedReportVideoMoments.tsx';
import { parseKeyMomentTime } from '../src/lib/keyMomentTime.ts';
import type { KeyMomentEditorState, PublicMatchReport } from '../src/types.ts';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/matches/published-merged-test/report?dev=1' });
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
  playCalls = 0;
  currentTime = 77.5;

  constructor(
    readonly target: HTMLIFrameElement,
    private readonly options: { events?: { onReady?: (event: { target: MockYouTubePlayer }) => void } },
  ) {}

  seekTo(seconds: number, allowSeekAhead: boolean) { this.seekCalls.push([seconds, allowSeekAhead]); }
  playVideo() { this.playCalls += 1; }
  getCurrentTime() { return this.currentTime; }
  destroy() {}
  ready() { this.options.events?.onReady?.({ target: this }); }
}

const report = {
  schema_version: 'public_match_report.v1', generated_at: '2026-09-10T00:00:00Z', id: 'published-merged-test', source_match_id: 'group', report_type: 'public_match_report',
  match: { id: 'published-merged-test', title: 'Corgi – Verisk', duration_sec: 300 },
  teams: [{ team_id: 'corgi', team_name: 'Corgi' }, { team_id: 'verisk', team_name: 'Verisk' }], players: [],
  key_moments: { schema_version: 'key_moments.v1', moments: [{ moment_id: 'first', time_sec: 40, headline: 'Pierwszy', origin: 'manual', public_category: 'other' }] },
} as PublicMatchReport;

function editorState(overrides: Partial<KeyMomentEditorState> = {}): KeyMomentEditorState {
  return {
    key_moment_editor_allowed: true,
    revision: 'r1',
    moments: [
      { moment_id: 'accepted-one', time_sec: 40, category: 'other', headline: 'Zaakceptowany moment', origin: 'manual' },
      { moment_id: 'legacy-no-team', time_sec: 80, category: 'other', headline: 'Historyczny moment', origin: 'manual' },
    ],
    suggestions: {
      status: 'ready', candidate_generation_digest: 'lineage-a', candidate_count: 1, unreviewed_count: 1,
      candidates: [{ candidate_id: 'candidate-verisk', start_time_sec: 100, peak_time_sec: 105, end_time_sec: 112, team_id: 'verisk', interestingness_score: .8, confidence: .7, evidence: [{ kind: 'progressive_pass_sequence' }, { kind: 'regain' }] }],
      overlaps: { 'candidate-verisk': { kind: 'extension', overlap_sec: 5, moment_id: 'accepted-one', headline: 'Zaakceptowany moment', start_time_sec: 102, end_time_sec: 108 } },
    },
    ...overrides,
  };
}

const externalVideo = {
  group_id: 'group', status: 'current' as const,
  external_video: { provider: 'youtube' as const, video_id: 'AbCdEfGhI_1', source_url: 'https://www.youtube.com/watch?v=AbCdEfGhI_1', embed_url: 'https://www.youtube-nocookie.com/embed/AbCdEfGhI_1', linked_video: { generation_id: 'g', input_semantic_digest: 'input', output_semantic_digest: 'output', timeline_span_sec: 300 }, updated_at: 'now' },
};

function installPlayer(): MockYouTubePlayer[] {
  const players: MockYouTubePlayer[] = [];
  class Player extends MockYouTubePlayer {
    constructor(target: HTMLIFrameElement, options: ConstructorParameters<typeof MockYouTubePlayer>[1]) {
      super(target, options); players.push(this);
    }
  }
  window.YT = { Player };
  return players;
}

function renderOperator(state = editorState(), callbacks: Partial<React.ComponentProps<typeof RedesignedReportVideoMoments>> = {}) {
  return render(React.createElement(RedesignedReportVideoMoments, {
    report, externalVideo, editorState: state,
    onSaveEditor: async () => state,
    onAcceptSuggestion: async () => state,
    onRejectSuggestion: async () => state,
    ...callbacks,
  }));
}

afterEach(() => {
  cleanup();
  window.YT = undefined;
  window.confirm = originalConfirm;
  document.body.style.overflow = '';
});

test('Key Moment clock parser keeps MM:SS, decimal clock, and seconds semantics', () => {
  assert.equal(parseKeyMomentTime('27:43'), 1663);
  assert.equal(parseKeyMomentTime('27:43.5'), 1663.5);
  assert.equal(parseKeyMomentTime('1663.5'), 1663.5);
  assert.equal(parseKeyMomentTime('bad'), null);
});

test('public viewer has no operator tabs or suggested controls', () => {
  const view = render(React.createElement(RedesignedReportVideoMoments, { report, externalVideo }));
  assert.equal(view.queryByRole('tablist'), null);
  assert.equal(view.queryByText(/Sugerowane Key Moments/), null);
  assert.equal(view.queryByRole('button', { name: '+ Dodaj moment' }), null);
});

test('operator starts on Accepted and Suggested playback keeps the one persistent player', async () => {
  const players = installPlayer();
  const view = renderOperator();
  const iframe = view.container.querySelector('iframe');
  await waitFor(() => assert.equal(players.length, 1));
  await act(async () => { players[0].ready(); });

  assert.equal(view.getByRole('tab', { name: 'Zaakceptowane 2' }).getAttribute('aria-selected'), 'true');
  fireEvent.click(view.getByRole('tab', { name: 'Sugestie 1' }));
  assert.ok(view.getByText('Sugerowane Key Moments (1)'));
  fireEvent.click(view.getByRole('button', { name: 'Odtwórz' }));

  assert.deepEqual(players[0].seekCalls, [[100, true]]);
  assert.equal(players[0].playCalls, 1);
  assert.equal(players.length, 1);
  assert.equal(view.container.querySelectorAll('iframe').length, 1);
  assert.equal(view.container.querySelector('iframe'), iframe);
});

test('expanded operator Suggested review reuses the same player', async () => {
  const players = installPlayer();
  const view = renderOperator();
  await waitFor(() => assert.equal(players.length, 1));
  await act(async () => { players[0].ready(); });
  const iframe = view.container.querySelector('iframe');
  fireEvent.click(view.getByRole('button', { name: 'Rozszerz analizę' }));
  assert.ok(view.getByRole('dialog', { name: 'Rozszerzona analiza meczu' }));
  fireEvent.click(view.getByRole('tab', { name: 'Sugestie 1' }));
  fireEvent.click(view.getByRole('button', { name: 'Odtwórz' }));
  assert.deepEqual(players[0].seekCalls, [[100, true]]);
  assert.equal(players.length, 1);
  assert.equal(view.container.querySelector('iframe'), iframe);
});

test('Add shows one focused form, validates new data, and cancel has no mutation', () => {
  let saves = 0;
  const view = renderOperator(editorState(), { onSaveEditor: async () => { saves += 1; return editorState(); } });
  fireEvent.click(view.getByRole('button', { name: '+ Dodaj moment' }));
  assert.ok(view.getByRole('heading', { name: 'Nowy Key Moment' }));
  assert.equal(view.queryByText('Zaakceptowany moment'), null);
  assert.equal(view.queryByRole('tablist'), null);
  assert.equal((view.getByLabelText('Czas momentu') as HTMLInputElement).value, '');

  fireEvent.click(view.getByRole('button', { name: 'Zapisz moment' }));
  assert.match(view.getByText(/Podaj czas/).textContent || '', /Podaj czas/);
  fireEvent.change(view.getByLabelText('Czas momentu'), { target: { value: '2:00' } });
  fireEvent.change(view.getByLabelText('Tytuł momentu'), { target: { value: 'Tymczasowy tytuł' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz moment' }));
  assert.match(view.getByText(/Wybierz drużynę/).textContent || '', /Wybierz drużynę/);
  fireEvent.change(view.getByLabelText('Drużyna momentu'), { target: { value: 'corgi' } });
  fireEvent.change(view.getByLabelText('Tytuł momentu'), { target: { value: '   ' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz moment' }));
  assert.match(view.getByText(/Tytuł momentu jest wymagany/).textContent || '', /Tytuł momentu jest wymagany/);

  fireEvent.click(view.getByRole('button', { name: 'Anuluj' }));
  assert.equal(saves, 0);
  assert.ok(view.getByText('Zaakceptowany moment'));
  assert.equal(view.getByRole('tab', { name: 'Zaakceptowane 2' }).getAttribute('aria-selected'), 'true');
});

test('Add uses current player time and saves an item from the latest editor state', async () => {
  const players = installPlayer();
  let saved: unknown;
  const view = renderOperator(editorState(), { onSaveEditor: async (draft) => { saved = draft; return editorState({ revision: 'r2', moments: draft.moments }); } });
  await waitFor(() => assert.equal(players.length, 1));
  await act(async () => { players[0].ready(); });
  fireEvent.click(view.getByRole('button', { name: '+ Dodaj moment' }));
  assert.equal((view.getByLabelText('Czas momentu') as HTMLInputElement).value, '1:17.5');
  fireEvent.change(view.getByLabelText('Drużyna momentu'), { target: { value: 'corgi' } });
  fireEvent.change(view.getByLabelText('Tytuł momentu'), { target: { value: 'Nowa akcja' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz moment' }));
  await waitFor(() => assert.ok(saved));
  const draft = saved as { expected_revision: string; moments: Array<{ time_sec: number; headline: string }> };
  assert.equal(draft.expected_revision, 'r1');
  assert.deepEqual(draft.moments.map((moment) => moment.headline), ['Nowa akcja', 'Zaakceptowany moment', 'Historyczny moment']);
  assert.equal(draft.moments[0].time_sec, 77.5);
  assert.equal(view.getByRole('tab', { name: 'Zaakceptowane 2' }).getAttribute('aria-selected'), 'true');
});

test('an open form retains its original revision instead of rebasing over a newer editor state', async () => {
  let saved: unknown;
  const initial = editorState();
  const props = {
    report, externalVideo, editorState: initial,
    onSaveEditor: async (draft: { expected_revision: string; moments: KeyMomentEditorState['moments'] }) => { saved = draft; return initial; },
    onAcceptSuggestion: async () => initial,
    onRejectSuggestion: async () => initial,
  };
  const view = render(React.createElement(RedesignedReportVideoMoments, props));
  fireEvent.click(view.getByRole('button', { name: '+ Dodaj moment' }));
  fireEvent.change(view.getByLabelText('Czas momentu'), { target: { value: '2:00' } });
  fireEvent.change(view.getByLabelText('Drużyna momentu'), { target: { value: 'corgi' } });
  fireEvent.change(view.getByLabelText('Tytuł momentu'), { target: { value: 'Zapis z r1' } });
  view.rerender(React.createElement(RedesignedReportVideoMoments, { ...props, editorState: editorState({ revision: 'r2' }) }));
  fireEvent.click(view.getByRole('button', { name: 'Zapisz moment' }));
  await waitFor(() => assert.ok(saved));
  assert.equal((saved as { expected_revision: string }).expected_revision, 'r1');
});

test('Edit uses the same form and preserves compatibility with a legacy no-team moment', async () => {
  let saved: unknown;
  const view = renderOperator(editorState(), { onSaveEditor: async (draft) => { saved = draft; return editorState(); } });
  fireEvent.click(view.getAllByRole('button', { name: 'Edytuj' })[1]);
  assert.ok(view.getByRole('heading', { name: 'Edytuj Key Moment' }));
  assert.equal((view.getByLabelText('Drużyna momentu') as HTMLSelectElement).value, '');
  assert.equal(view.queryByRole('tablist'), null);
  fireEvent.click(view.getByRole('button', { name: 'Anuluj' }));
  assert.equal(saved, undefined);
  assert.equal(view.getByRole('tab', { name: 'Zaakceptowane 2' }).getAttribute('aria-selected'), 'true');
  fireEvent.click(view.getAllByRole('button', { name: 'Edytuj' })[1]);
  fireEvent.change(view.getByLabelText('Tytuł momentu'), { target: { value: 'Poprawiony historyczny moment' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz moment' }));
  await waitFor(() => assert.ok(saved));
  const draft = saved as { moments: Array<{ moment_id?: string; team_id?: string | null; headline: string }> };
  const legacy = draft.moments.find((moment) => moment.moment_id === 'legacy-no-team');
  assert.equal(legacy?.team_id, undefined);
  assert.equal(legacy?.headline, 'Poprawiony historyczny moment');
});

test('Accept opens the shared blank form, cancel leaves candidate unreviewed, and save returns to Suggested', async () => {
  let accepted: unknown;
  const initial = editorState();
  const view = renderOperator(initial, { onAcceptSuggestion: async (draft) => { accepted = draft; return editorState({ revision: 'r2', moments: [...(initial.moments || []), draft.moment], suggestions: { ...initial.suggestions!, candidates: [], unreviewed_count: 0 } }); } });
  fireEvent.click(view.getByRole('tab', { name: 'Sugestie 1' }));
  fireEvent.click(view.getByRole('button', { name: 'Akceptuj' }));
  assert.ok(view.getByRole('heading', { name: 'Akceptuj sugerowany moment' }));
  assert.equal(view.queryByRole('tablist'), null);
  assert.match((view.getByLabelText('Czas momentu') as HTMLInputElement).value, /^1:40(?:\.0)?$/);
  assert.equal((view.getByLabelText('Drużyna momentu') as HTMLSelectElement).value, 'verisk');
  assert.equal((view.getByLabelText('Tytuł momentu') as HTMLInputElement).value, '');
  fireEvent.click(view.getByRole('button', { name: 'Anuluj' }));
  assert.equal(accepted, undefined);
  assert.ok(view.getByText('Sugerowane Key Moments (1)'));
  assert.equal(view.getByRole('tab', { name: 'Sugestie 1' }).getAttribute('aria-selected'), 'true');

  fireEvent.click(view.getByRole('button', { name: 'Akceptuj' }));
  fireEvent.change(view.getByLabelText('Tytuł momentu'), { target: { value: 'Finalny opis akcji' } });
  fireEvent.click(view.getByRole('button', { name: 'Zapisz moment' }));
  await waitFor(() => assert.ok(accepted));
  const draft = accepted as { expected_revision: string; candidate_id: string; moment: { time_sec: number; team_id?: string | null; headline: string } };
  assert.equal(draft.expected_revision, 'r1');
  assert.equal(draft.candidate_id, 'candidate-verisk');
  assert.deepEqual(draft.moment, { time_sec: 100, team_id: 'verisk', player_id: null, category: 'other', headline: 'Finalny opis akcji', note: '', origin: 'manual' });
  assert.equal(view.getByRole('tab', { name: 'Sugestie 1' }).getAttribute('aria-selected'), 'true');
});

test('Reject remains on Suggested and delete persists the replacement list', async () => {
  let rejected: unknown;
  let saved: unknown;
  const view = renderOperator(editorState(), {
    onRejectSuggestion: async (draft) => { rejected = draft; return editorState({ revision: 'r2', suggestions: { ...editorState().suggestions!, candidates: [], unreviewed_count: 0 } }); },
    onSaveEditor: async (draft) => { saved = draft; return editorState({ revision: 'r2', moments: draft.moments }); },
  });
  fireEvent.click(view.getByRole('tab', { name: 'Sugestie 1' }));
  fireEvent.click(view.getByRole('button', { name: 'Odrzuć' }));
  await waitFor(() => assert.ok(rejected));
  assert.deepEqual(rejected, { expected_revision: 'r1', candidate_id: 'candidate-verisk', candidate_generation_digest: 'lineage-a' });
  assert.equal(view.getByRole('tab', { name: 'Sugestie 1' }).getAttribute('aria-selected'), 'true');

  fireEvent.click(view.getByRole('tab', { name: 'Zaakceptowane 2' }));
  window.confirm = () => true;
  fireEvent.click(view.getAllByRole('button', { name: 'Usuń' })[0]);
  await waitFor(() => assert.ok(saved));
  assert.deepEqual((saved as { moments: Array<{ moment_id?: string }> }).moments.map((moment) => moment.moment_id), ['legacy-no-team']);
});
