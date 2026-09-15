import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React from 'react';

import { RedesignedReportVideoMoments } from '../src/components/RedesignedReportVideoMoments.tsx';
import type { PublicMatchReport } from '../src/types.ts';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/report' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { act, cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');

class MockYouTubePlayer {
  readonly seekCalls: Array<[number, boolean]> = [];
  playCalls = 0;
  destroyCalls = 0;

  constructor(
    readonly target: HTMLIFrameElement,
    private readonly options: { events?: { onReady?: (event: { target: MockYouTubePlayer }) => void } },
  ) {}

  seekTo(seconds: number, allowSeekAhead: boolean) { this.seekCalls.push([seconds, allowSeekAhead]); }
  playVideo() { this.playCalls += 1; }
  destroy() { this.destroyCalls += 1; }
  ready() { this.options.events?.onReady?.({ target: this }); }
}

const report = {
  schema_version: 'public_match_report.v1', generated_at: '2026-09-10T00:00:00Z', id: 'published-merged-test', source_match_id: 'group', report_type: 'public_match_report',
  match: { id: 'published-merged-test', title: 'Corgi – Verisk', duration_sec: 120 }, teams: [], players: [],
  key_moments: { schema_version: 'key_moments.v1', moments: [
    { moment_id: 'first', time_sec: 146, headline: 'Pierwszy moment', origin: 'manual', public_category: 'other' },
    { moment_id: 'second', time_sec: 261, headline: 'Drugi moment', origin: 'manual', public_category: 'other' },
  ] },
} as PublicMatchReport;

function installPlayer(): MockYouTubePlayer[] {
  const players: MockYouTubePlayer[] = [];
  class Player extends MockYouTubePlayer {
    constructor(target: HTMLIFrameElement, options: ConstructorParameters<typeof MockYouTubePlayer>[1]) {
      super(target, options);
      players.push(this);
    }
  }
  window.YT = { Player };
  return players;
}

function renderModule() {
  return render(React.createElement(RedesignedReportVideoMoments, {
    report,
    externalVideo: {
      group_id: 'group', status: 'current', external_video: {
        provider: 'youtube', video_id: 'AbCdEfGhI_1', source_url: 'https://www.youtube.com/watch?v=AbCdEfGhI_1', embed_url: 'https://www.youtube-nocookie.com/embed/AbCdEfGhI_1',
        linked_video: { generation_id: 'g', input_semantic_digest: 'input', output_semantic_digest: 'output', timeline_span_sec: 120 }, updated_at: 'now',
      },
    },
    editorAllowed: false,
    onEditKeyMoments: () => undefined,
  }));
}

afterEach(() => {
  cleanup();
  window.YT = undefined;
  document.body.style.overflow = '';
});

test('Key Moments seek one persistent no-cookie YouTube player without replacing its iframe', async () => {
  const players = installPlayer();
  const view = renderModule();
  const iframe = view.container.querySelector('iframe');
  assert.ok(iframe);
  assert.match(iframe.getAttribute('src') || '', /^https:\/\/www\.youtube-nocookie\.com\/embed\/AbCdEfGhI_1\?/);
  assert.match(iframe.getAttribute('src') || '', /enablejsapi=1/);
  const playButtons = view.getAllByRole('button', { name: 'Odtwórz' });
  assert.equal(playButtons.length, 2);
  assert.ok(playButtons.every((button) => button.classList.contains('key-moment-action-button')));
  assert.ok(playButtons.every((button) => button.textContent === '▶'));
  await waitFor(() => assert.equal(players.length, 1));
  await act(async () => { players[0].ready(); });

  fireEvent.click(playButtons[0]);
  fireEvent.click(playButtons[1]);

  assert.deepEqual(players[0].seekCalls, [[146, true], [261, true]]);
  assert.equal(players[0].playCalls, 2);
  assert.equal(players.length, 1);
  assert.equal(view.container.querySelectorAll('iframe').length, 1);
  assert.equal(view.container.querySelector('iframe'), iframe);
});

test('latest Key Moment click is retained until the YouTube player is ready', async () => {
  const players = installPlayer();
  const view = renderModule();
  await waitFor(() => assert.equal(players.length, 1));

  fireEvent.click(view.getAllByRole('button', { name: 'Odtwórz' })[0]);
  fireEvent.click(view.getAllByRole('button', { name: 'Odtwórz' })[1]);
  assert.deepEqual(players[0].seekCalls, []);
  await act(async () => { players[0].ready(); });
  assert.deepEqual(players[0].seekCalls, [[261, true]]);
  assert.equal(players[0].playCalls, 1);
});

test('desktop expanded analysis keeps the same player, closes by control and Escape, and marks its desktop-only affordance', async () => {
  const players = installPlayer();
  const view = renderModule();
  await waitFor(() => assert.equal(players.length, 1));
  const iframe = view.container.querySelector('iframe');
  const expand = view.getByRole('button', { name: 'Rozszerz analizę' });
  assert.equal(expand.getAttribute('data-desktop-only'), 'true');

  fireEvent.click(expand);
  assert.ok(view.getByRole('dialog', { name: 'Rozszerzona analiza meczu' }));
  assert.equal(view.container.querySelectorAll('iframe').length, 1);
  assert.equal(view.container.querySelector('iframe'), iframe);
  assert.equal(players.length, 1);
  fireEvent.click(view.getAllByRole('button', { name: 'Odtwórz' })[0]);
  await act(async () => { players[0].ready(); });
  assert.deepEqual(players[0].seekCalls, [[146, true]]);

  fireEvent.click(view.getByRole('button', { name: 'Zwiń analizę' }));
  assert.equal(view.queryByRole('dialog', { name: 'Rozszerzona analiza meczu' }), null);
  assert.equal(view.container.querySelector('iframe'), iframe);
  fireEvent.click(view.getByRole('button', { name: 'Rozszerz analizę' }));
  fireEvent.keyDown(window, { key: 'Escape' });
  assert.equal(view.queryByRole('dialog', { name: 'Rozszerzona analiza meczu' }), null);
});

test('static canonical shots use the existing player and expose two separate read-only team maps', async () => {
  const players = installPlayer();
  const publicShotReport: PublicMatchReport = {
    ...report,
    teams: [{ team_id: 'corgi', team_name: 'Corgi' }, { team_id: 'verisk', team_name: 'Verisk' }],
    players: [{ player_id: 'krzysiek', player_name: 'Krzysiek', team_id: 'corgi' }],
    shots: [
      { shot_id: 'goal', time_sec: 10, team_id: 'corgi', outcome: 'goal', player_id: 'krzysiek', location_m: { x: 10, y: 12 }, map_location: { x: .25, y: .3 } },
      { shot_id: 'blocked', time_sec: 20, team_id: 'verisk', outcome: 'blocked', location_m: null },
      { shot_id: 'off', time_sec: 30, team_id: 'verisk', outcome: 'off_target', location_m: { x: 20, y: 30 } },
    ],
  };
  const view = render(React.createElement(RedesignedReportVideoMoments, {
    report: publicShotReport,
    externalVideo: { group_id: 'group', status: 'current', external_video: { provider: 'youtube', video_id: 'AbCdEfGhI_1', source_url: 'https://www.youtube.com/watch?v=AbCdEfGhI_1', embed_url: 'https://www.youtube-nocookie.com/embed/AbCdEfGhI_1', linked_video: { generation_id: 'g', input_semantic_digest: 'input', output_semantic_digest: 'output', timeline_span_sec: 120 }, updated_at: 'now' } },
  }));
  await waitFor(() => assert.equal(players.length, 1));
  await act(async () => { players[0].ready(); });
  fireEvent.click(view.getByRole('tab', { name: 'Strzały' }));
  assert.equal(view.getAllByLabelText(/Mapa strzałów:/).length, 2);
  assert.equal(view.container.querySelectorAll('.public-shot-map-marker').length, 1);
  assert.match(view.container.textContent || '', /0 z 2 strzałów na mapie/);
  assert.match(view.container.textContent || '', /Strzał niecelny/);
  assert.equal(view.queryByText(/Sugestie/), null);
  assert.equal(view.queryByRole('button', { name: 'Dodaj strzał' }), null);
  const marker = view.getByRole('button', { name: /0:10.*Gol.*Corgi.*Krzysiek/ });
  assert.match(marker.getAttribute('title') || '', /0:10.*Gol.*Corgi.*Krzysiek/);
  fireEvent.click(marker);
  assert.deepEqual(players[0].seekCalls, [[10, true]]);
  assert.equal(players.length, 1);
  assert.equal(view.container.querySelectorAll('iframe').length, 1);
});

test('one outcome filter controls both maps without changing the public shot browser or summary', async () => {
  const players = installPlayer();
  const publicShotReport: PublicMatchReport = {
    ...report,
    teams: [{ team_id: 'corgi', team_name: 'Corgi' }, { team_id: 'verisk', team_name: 'Verisk' }],
    players: [{ player_id: 'krzysiek', player_name: 'Krzysiek', team_id: 'corgi' }],
    shots: [
      { shot_id: 'goal', time_sec: 10, team_id: 'corgi', outcome: 'goal', player_id: 'krzysiek', map_location: { x: .2, y: .2 } },
      { shot_id: 'on', time_sec: 12, team_id: 'corgi', outcome: 'on_target', map_location: { x: .3, y: .3 } },
      { shot_id: 'off', time_sec: 30, team_id: 'verisk', outcome: 'off_target', map_location: { x: .4, y: .4 } },
      { shot_id: 'blocked', time_sec: 32, team_id: 'verisk', outcome: 'blocked', map_location: { x: .5, y: .5 } },
    ],
  };
  const view = render(React.createElement(RedesignedReportVideoMoments, {
    report: publicShotReport,
    externalVideo: { group_id: 'group', status: 'current', external_video: { provider: 'youtube', video_id: 'AbCdEfGhI_1', source_url: 'https://www.youtube.com/watch?v=AbCdEfGhI_1', embed_url: 'https://www.youtube-nocookie.com/embed/AbCdEfGhI_1', linked_video: { generation_id: 'g', input_semantic_digest: 'input', output_semantic_digest: 'output', timeline_span_sec: 120 }, updated_at: 'now' } },
  }));
  await waitFor(() => assert.equal(players.length, 1));
  await act(async () => { players[0].ready(); });
  fireEvent.click(view.getByRole('tab', { name: 'Strzały' }));
  for (const label of ['Wszystkie', 'Celne', 'Niecelne', 'Zablokowane', 'Gole']) assert.ok(view.getByRole('button', { name: new RegExp(label) }));
  assert.equal(view.container.querySelectorAll('.public-shot-map-marker').length, 4);
  assert.match(view.container.textContent || '', /Opublikowane strzały \(4\)/);
  const goalMarker = view.getByRole('button', { name: /0:10.*Gol.*Corgi.*Krzysiek/ });
  const onTargetMarker = view.getByRole('button', { name: /0:12.*Strzał celny.*Corgi/ });
  const offTargetMarker = view.getByRole('button', { name: /0:30.*Strzał niecelny.*Verisk/ });
  const blockedMarker = view.getByRole('button', { name: /0:32.*Strzał zablokowany.*Verisk/ });
  assert.equal(goalMarker.textContent, '⚽');
  assert.equal(onTargetMarker.textContent, '●');
  assert.equal(offTargetMarker.textContent, '○');
  assert.equal(blockedMarker.textContent, '×');
  assert.match(goalMarker.getAttribute('title') || '', /0:10.*Gol.*Corgi.*Krzysiek/);
  fireEvent.click(goalMarker);
  assert.match(view.getByRole('status').textContent || '', /Krzysiek/);
  fireEvent.click(view.getByRole('button', { name: /Celne/ }));
  assert.equal(view.container.querySelectorAll('.public-shot-map-marker').length, 2);
  assert.match(view.getByRole('status').textContent || '', /Krzysiek/);
  assert.equal(view.getByRole('button', { name: /Celne/ }).getAttribute('aria-pressed'), 'true');
  assert.match(view.container.textContent || '', /Opublikowane strzały \(4\)/);
  fireEvent.click(view.getByRole('button', { name: /Niecelne/ }));
  assert.equal(view.container.querySelectorAll('.public-shot-map-marker').length, 1);
  assert.equal(view.queryByRole('status'), null);
  fireEvent.click(view.getByRole('button', { name: /Zablokowane/ }));
  assert.equal(view.container.querySelectorAll('.public-shot-map-marker').length, 1);
  fireEvent.click(view.getByRole('button', { name: /Gole/ }));
  assert.equal(view.container.querySelectorAll('.public-shot-map-marker').length, 1);
  assert.equal(players.length, 1);
  assert.equal(view.container.querySelectorAll('iframe').length, 1);
});
