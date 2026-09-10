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
  await waitFor(() => assert.equal(players.length, 1));
  await act(async () => { players[0].ready(); });

  fireEvent.click(view.getAllByRole('button', { name: 'Odtwórz' })[0]);
  fireEvent.click(view.getAllByRole('button', { name: 'Odtwórz' })[1]);

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
