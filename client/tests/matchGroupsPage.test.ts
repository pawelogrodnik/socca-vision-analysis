import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { JSDOM } from 'jsdom';
import React from 'react';
import { BrowserRouter, MemoryRouter, Route, Routes } from 'react-router-dom';

import { MatchGroupsPage } from '../src/components/MatchGroupsPage.tsx';
import { AggregateMatchReportContent } from '../src/components/AggregateMatchReportContent.tsx';
import { AggregateKeyMoments, youtubeWatchUrl } from '../src/components/AggregateKeyMoments.tsx';
import { AggregateMatchReportPage } from '../src/components/AggregateMatchReportPage.tsx';
import { MatchGroupExternalVideoSection } from '../src/components/MatchGroupExternalVideoSection.tsx';
import type { AggregatePublicMatchReport } from '../src/types.ts';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/match-groups' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { act, cleanup, fireEvent, render, waitFor } = await import('@testing-library/react');

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

test('match-group page exposes background combined-video generation without treating missing source video as ready', async () => {
  const calls: string[] = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const path = String(input);
    calls.push(path);
    if (path.endsWith('/eligible-sources')) return Response.json([]);
    if (path.endsWith('/match-groups') && !init?.method) return Response.json([{
      group: { group_id: 'group-1', metadata: { title: 'Mecz' }, members: [{ published_id: 'physical-a' }, { published_id: 'physical-b' }], timing: { analyzed_duration_sec: 900, timeline_span_sec: 900, mapping: 'ordered' }, compatibility: { status: 'compatible', blocking_reasons: [] } },
      validation: { status: 'compatible', blocking_reasons: [] },
    }]);
    if (path.endsWith('/group-1/video') && init?.method === 'POST') return Response.json({ group_id: 'group-1', status: 'generating' });
    if (path.endsWith('/group-1/video')) return Response.json({ group_id: 'group-1', status: 'unavailable_source_video', reason: 'unavailable_source_video' });
    throw new Error(`Unexpected ${path}`);
  };
  try {
    const view = render(React.createElement(BrowserRouter, null, React.createElement(MatchGroupsPage)));
    await waitFor(() => assert.ok(view.getByText(/Brak wideo źródłowego/)));
    fireEvent.click(view.getByRole('button', { name: 'Generuj wideo' }));
    await waitFor(() => assert.ok(calls.some((path) => path.endsWith('/group-1/video/generate'))));
    assert.equal(view.queryByRole('link', { name: 'Otwórz wideo' }), null);
  } finally { globalThis.fetch = originalFetch; }
});

test('YouTube settings submit only when the local combined video is ready', async () => {
  const saves: Array<[string, string]> = [];
  let view: ReturnType<typeof render>;
  await act(async () => {
    view = render(React.createElement(MatchGroupExternalVideoSection, {
      groupId: 'group-1', localVideo: { group_id: 'group-1', status: 'ready' }, externalVideo: { group_id: 'group-1', status: 'stale', external_video: { provider: 'youtube', video_id: 'AbCdEfGhI_1', source_url: 'https://youtu.be/AbCdEfGhI_1', linked_video: { generation_id: 'old', input_semantic_digest: 'old', output_semantic_digest: 'old', timeline_span_sec: 10 }, updated_at: 'now' } }, busy: false,
      onSave: async (...args: [string, string]) => { saves.push(args); }, onRemove: async () => undefined,
    }));
  });
  await waitFor(() => assert.equal(view!.getByRole('button', { name: 'Zapisz link YouTube' }).hasAttribute('disabled'), false));
  fireEvent.click(view!.getByRole('button', { name: 'Zapisz link YouTube' }));
  await waitFor(() => assert.deepEqual(saves, [['group-1', 'https://youtu.be/AbCdEfGhI_1']]));
});

test('match-group page disables deletion while an initial combined-video generation is active', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input, init) => {
    const path = String(input);
    if (path.endsWith('/eligible-sources')) return Response.json([]);
    if (path.endsWith('/match-groups') && !init?.method) return Response.json([{
      group: { group_id: 'group-1', metadata: { title: 'Mecz' }, members: [{ published_id: 'physical-a' }, { published_id: 'physical-b' }], timing: { analyzed_duration_sec: 900, timeline_span_sec: 900, mapping: 'ordered' }, compatibility: { status: 'compatible', blocking_reasons: [] } },
      validation: { status: 'compatible', blocking_reasons: [] },
    }]);
    if (path.endsWith('/group-1/video')) return Response.json({ group_id: 'group-1', status: 'generating', last_attempt: { status: 'generating' } });
    throw new Error(`Unexpected ${path}`);
  };
  try {
    const view = render(React.createElement(BrowserRouter, null, React.createElement(MatchGroupsPage)));
    await waitFor(() => assert.equal(view.getByRole('button', { name: 'Usuń' }).hasAttribute('disabled'), true));
  } finally { globalThis.fetch = originalFetch; }
});

test('match-group page keeps polling a ready video while its replacement regenerates', async () => {
  const originalFetch = globalThis.fetch;
  const originalSetTimeout = window.setTimeout;
  const scheduled: Array<() => void> = [];
  let videoReads = 0;
  window.setTimeout = ((callback: TimerHandler) => {
    scheduled.push(callback as () => void);
    return scheduled.length as unknown as number;
  }) as typeof window.setTimeout;
  globalThis.fetch = async (input, init) => {
    const path = String(input);
    if (path.endsWith('/eligible-sources')) return Response.json([]);
    if (path.endsWith('/match-groups') && !init?.method) return Response.json([{
      group: { group_id: 'group-1', metadata: { title: 'Mecz' }, members: [{ published_id: 'physical-a' }, { published_id: 'physical-b' }], timing: { analyzed_duration_sec: 900, timeline_span_sec: 900, mapping: 'ordered' }, compatibility: { status: 'compatible', blocking_reasons: [] } },
      validation: { status: 'compatible', blocking_reasons: [] },
    }]);
    if (path.endsWith('/group-1/video')) {
      videoReads += 1;
      return Response.json(videoReads === 1
        ? { group_id: 'group-1', status: 'ready', generation_id: 'generation-a', artifact_url: '/api/published/match-groups/group-1/video/generations/generation-a/file', last_attempt: { status: 'generating' } }
        : { group_id: 'group-1', status: 'ready', generation_id: 'generation-b', artifact_url: '/api/published/match-groups/group-1/video/generations/generation-b/file' });
    }
    throw new Error(`Unexpected ${path}`);
  };
  try {
    const view = render(React.createElement(BrowserRouter, null, React.createElement(MatchGroupsPage)));
    await waitFor(() => assert.ok(view.getByText(/Gotowe.*trwa regeneracja/)));
    assert.equal((view.getByRole('link', { name: 'Otwórz wideo' }) as HTMLAnchorElement).getAttribute('href'), '/api/published/match-groups/group-1/video/generations/generation-a/file');
    assert.equal(view.getByRole('button', { name: 'Usuń' }).hasAttribute('disabled'), true);
    scheduled.shift()?.();
    await waitFor(() => assert.ok(view.getByRole('link', { name: 'Otwórz wideo' })));
    assert.equal(videoReads, 2);
    assert.equal((view.getByRole('link', { name: 'Otwórz wideo' }) as HTMLAnchorElement).getAttribute('href'), '/api/published/match-groups/group-1/video/generations/generation-b/file');
    assert.equal(view.getByRole('button', { name: 'Usuń' }).hasAttribute('disabled'), false);
  } finally {
    globalThis.fetch = originalFetch;
    window.setTimeout = originalSetTimeout;
  }
});

test('match-group page keeps the old video visible after a failed regeneration', async () => {
  const originalFetch = globalThis.fetch;
  const originalSetTimeout = window.setTimeout;
  const scheduled: Array<() => void> = [];
  let videoReads = 0;
  window.setTimeout = ((callback: TimerHandler) => {
    scheduled.push(callback as () => void);
    return scheduled.length as unknown as number;
  }) as typeof window.setTimeout;
  globalThis.fetch = async (input, init) => {
    const path = String(input);
    if (path.endsWith('/eligible-sources')) return Response.json([]);
    if (path.endsWith('/match-groups') && !init?.method) return Response.json([{
      group: { group_id: 'group-1', metadata: { title: 'Mecz' }, members: [{ published_id: 'physical-a' }, { published_id: 'physical-b' }], timing: { analyzed_duration_sec: 900, timeline_span_sec: 900, mapping: 'ordered' }, compatibility: { status: 'compatible', blocking_reasons: [] } },
      validation: { status: 'compatible', blocking_reasons: [] },
    }]);
    if (path.endsWith('/group-1/video')) {
      videoReads += 1;
      return Response.json(videoReads === 1
        ? { group_id: 'group-1', status: 'ready', generation_id: 'generation-a', artifact_url: '/api/published/match-groups/group-1/video/generations/generation-a/file', last_attempt: { status: 'generating' } }
        : { group_id: 'group-1', status: 'ready', generation_id: 'generation-a', artifact_url: '/api/published/match-groups/group-1/video/generations/generation-a/file', last_attempt: { status: 'failed', reason: 'video_generation_failed' } });
    }
    throw new Error(`Unexpected ${path}`);
  };
  try {
    const view = render(React.createElement(BrowserRouter, null, React.createElement(MatchGroupsPage)));
    await waitFor(() => assert.ok(view.getByText(/Gotowe.*trwa regeneracja/)));
    scheduled.shift()?.();
    await waitFor(() => assert.ok(view.getByText(/ostatnia regeneracja nie powiodła się/)));
    assert.equal((view.getByRole('link', { name: 'Otwórz wideo' }) as HTMLAnchorElement).getAttribute('href'), '/api/published/match-groups/group-1/video/generations/generation-a/file');
    assert.equal(view.getByRole('button', { name: 'Usuń' }).hasAttribute('disabled'), false);
  } finally {
    globalThis.fetch = originalFetch;
    window.setTimeout = originalSetTimeout;
  }
});

test('aggregate content renders per-team passes and server-rebased timelines', () => {
  const view = render(React.createElement(BrowserRouter, null, React.createElement(AggregateMatchReportContent, {
    report: {
      schema_version: '1', report_type: 'public_aggregate_match_report', group_id: 'group-1', match: { title: 'Full match' },
      source_match_ids: ['m1'], source_published_ids: ['p1'],
      sources: [{ published_id: 'p1', source_match_id: 'm1', sequence_index: 0, logical_offset_sec: 0 }],
      timing: { analyzed_duration_sec: 120, timeline_span_sec: 120, mapping: 'ordered' },
      stats_semantics: { ball: 'experimental_candidates' },
      spatial: { heatmaps: { status: 'not_available', reason: 'orientation' }, team_shape: { status: 'not_available', reason: 'orientation' } },
      teams: [
        { team_id: 'a', team_name: 'Corgi', movement: { status: 'ready', total_distance_m: 123, sprint_count: 2 } },
        { team_id: 'b', team_name: 'Verisk', movement: { status: 'ready', total_distance_m: 99, sprint_count: 1 } },
      ],
      players: [{ player_id: 'p', player_name: 'Piotr', team_id: 'a', movement: { status: 'ready', total_distance_m: 88, avg_speed_kmh: 10 } }],
      ball: {
        possession: { status: 'ready', known_frames: 20, possession_share_percent_by_team_id: { a: 60 } },
        passes: {
          status: 'ready', attempts: 8, completed: 5, failed: 3, completion_rate_percent: 62.5,
          attempts_by_team_id: { a: 5 }, completed_by_team_id: { a: 3 }, failed_by_team_id: { a: 2 }, completion_rate_percent_by_team_id: { a: 60 },
        },
      },
      identity_coverage: { status: 'ready', confirmed_observations: 10, reliable_observations: 12, confirmed_coverage_percent: 83.3 },
      timelines: {
        possession: { status: 'ready', windows: [
          { start_time_sec: 0, end_time_sec: 60, possession_share_percent_by_team_id: { a: 60 } },
          { start_time_sec: 60, end_time_sec: 120, possession_share_percent_by_team_id: { a: 40 } },
        ] },
        attacking_momentum: { product_readiness: 'experimental', status: 'completed', points: [
          { start_time_sec: 0, end_time_sec: 60, team_values_by_team_id: { a: 1.5 } },
          { start_time_sec: 60, end_time_sec: 120, team_values_by_team_id: { a: 0.75 } },
        ] },
      },
    },
  })));
  assert.ok(view.getByText('Podsumowanie drużyn'));
  assert.ok(view.getByText('Piotr'));
  assert.ok(view.getByText('5.0 / 3.0 / 2.0'));
  assert.equal(view.getAllByText('60.0%').length, 2);
  assert.match(view.getByText('Verisk').closest('tr')?.textContent || '', /— \/ — \/ —/);
  assert.ok(view.getByText('Posiadanie w czasie'));
  assert.ok(view.getByText(/Atakujące momentum/));
  assert.equal(view.getAllByText(/eksperymentalne/).length, 2);
  assert.ok(view.getByText(/Heatmapy: not_available/));
  assert.equal((view.getByRole('link', { name: 'Fragment 1' }) as HTMLAnchorElement).getAttribute('href'), '/published/matches/p1/report');
});

test('aggregate page shows server-authoritative stale reason above its last coherent report', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json({
    report: {
      schema_version: '1', report_type: 'public_aggregate_match_report', group_id: 'group-1', match: { title: 'Old report' }, source_match_ids: [], source_published_ids: [], sources: [],
      timing: { analyzed_duration_sec: 0, timeline_span_sec: 0, mapping: 'ordered' }, spatial: { heatmaps: { status: 'not_available' }, team_shape: { status: 'not_available' } }, teams: [], players: [],
    },
    validation: { status: 'stale', blocking_reasons: [{ code: 'source_generation_changed', detail: 'Jeden z raportów źródłowych został ponownie opublikowany.' }] },
  });
  try {
    const view = render(React.createElement(MemoryRouter, { initialEntries: ['/published/match-groups/group-1/report'] }, React.createElement(Routes, null,
      React.createElement(Route, { path: '/published/match-groups/:groupId/report', element: React.createElement(AggregateMatchReportPage) }),
    )));
    await waitFor(() => assert.ok(view.getByText('Raport jest nieaktualny.')));
    assert.ok(view.getByText('Jeden z raportów źródłowych został ponownie opublikowany.'));
    assert.ok(view.getByText('Old report'));
  } finally { globalThis.fetch = originalFetch; }
});

test('aggregate page embeds only the server-derived current YouTube URL and keeps local fallback', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const path = String(input);
    if (path.endsWith('/video')) return Response.json({ group_id: 'group-1', status: 'ready', artifact_url: '/local-video.mp4' });
    return Response.json({
      report: { schema_version: '1', report_type: 'public_aggregate_match_report', group_id: 'group-1', match: { title: 'Mecz' }, source_match_ids: [], source_published_ids: [], sources: [], timing: { analyzed_duration_sec: 120, timeline_span_sec: 120, mapping: 'ordered' }, spatial: { heatmaps: { status: 'not_available' }, team_shape: { status: 'not_available' } }, teams: [], players: [] },
      validation: { status: 'compatible', blocking_reasons: [] },
      external_video: { group_id: 'group-1', status: 'current', external_video: { provider: 'youtube', video_id: 'AbCdEfGhI_1', source_url: 'https://www.youtube.com/watch?v=AbCdEfGhI_1', embed_url: 'https://www.youtube-nocookie.com/embed/AbCdEfGhI_1', linked_video: { generation_id: 'a', input_semantic_digest: 'i', output_semantic_digest: 'o', timeline_span_sec: 120 }, updated_at: 'now' } },
    });
  };
  try {
    const view = render(React.createElement(MemoryRouter, { initialEntries: ['/published/match-groups/group-1/report'] }, React.createElement(Routes, null,
      React.createElement(Route, { path: '/published/match-groups/:groupId/report', element: React.createElement(AggregateMatchReportPage) }),
    )));
    await waitFor(() => assert.equal(view.container.querySelector('iframe')?.getAttribute('src'), 'https://www.youtube-nocookie.com/embed/AbCdEfGhI_1'));
    assert.equal(view.container.querySelector('iframe')?.hasAttribute('allowfullscreen'), true);
    assert.equal((view.getByRole('link', { name: 'Otwórz lokalne wideo' }) as HTMLAnchorElement).getAttribute('href'), '/local-video.mp4');
  } finally { globalThis.fetch = originalFetch; }
});

test('aggregate page polls a ready prior generation and switches to its replacement', async () => {
  const originalFetch = globalThis.fetch;
  const originalSetTimeout = window.setTimeout;
  const scheduled: Array<() => void> = [];
  let videoReads = 0;
  window.setTimeout = ((callback: TimerHandler) => {
    scheduled.push(callback as () => void);
    return scheduled.length as unknown as number;
  }) as typeof window.setTimeout;
  globalThis.fetch = async (input) => {
    const path = String(input);
    if (path.endsWith('/video')) {
      videoReads += 1;
      return Response.json(videoReads === 1
        ? { group_id: 'group-1', status: 'ready', generation_id: 'generation-a', artifact_url: '/api/published/match-groups/group-1/video/generations/generation-a/file', last_attempt: { status: 'generating' } }
        : { group_id: 'group-1', status: 'ready', generation_id: 'generation-b', artifact_url: '/api/published/match-groups/group-1/video/generations/generation-b/file' });
    }
    return Response.json({
      report: { schema_version: '1', report_type: 'public_aggregate_match_report', group_id: 'group-1', match: { title: 'Mecz' }, source_match_ids: [], source_published_ids: [], sources: [], timing: { analyzed_duration_sec: 120, timeline_span_sec: 120, mapping: 'ordered' }, spatial: { heatmaps: { status: 'not_available' }, team_shape: { status: 'not_available' } }, teams: [], players: [] },
      validation: { status: 'compatible', blocking_reasons: [] },
    });
  };
  try {
    const view = render(React.createElement(MemoryRouter, { initialEntries: ['/published/match-groups/group-1/report'] }, React.createElement(Routes, null,
      React.createElement(Route, { path: '/published/match-groups/:groupId/report', element: React.createElement(AggregateMatchReportPage) }),
    )));
    await waitFor(() => assert.ok(view.getByText(/trwa regeneracja nowszej wersji/)));
    assert.equal(view.container.querySelector('video')?.getAttribute('src'), '/api/published/match-groups/group-1/video/generations/generation-a/file');
    scheduled.shift()?.();
    await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Pełne wideo meczu' })));
    assert.equal(videoReads, 2);
    assert.equal(view.container.querySelector('video')?.getAttribute('src'), '/api/published/match-groups/group-1/video/generations/generation-b/file');
  } finally {
    globalThis.fetch = originalFetch;
    window.setTimeout = originalSetTimeout;
  }
});

function keyMomentReport(): AggregatePublicMatchReport {
  return {
    schema_version: '1.0.0', report_type: 'public_aggregate_match_report', group_id: 'group-1', match: { title: 'Mecz' },
    source_match_ids: [], source_published_ids: [], sources: [],
    timing: { analyzed_duration_sec: 900, timeline_span_sec: 900, mapping: 'ordered' },
    teams: [{ team_id: 'team-corgi', team_name: 'Corgi', movement: { status: 'ready' } }], players: [],
    spatial: { heatmaps: { status: 'not_available' }, team_shape: { status: 'not_available' } },
    key_moments: {
      schema_version: '1.0.0', policy_version: 'logical-key-moments:v1', timeline_semantics: 'logical_match_video', status: 'ready',
      moments: [{
        moment_id: 'km-1', time_sec: 722.5, window_start_sec: 720, window_end_sec: 725, type: 'momentum_peak', team_id: 'team-corgi', importance_score: 0.82,
        headline: 'Mocny okres przewagi', evidence: { primary_signal: 'attacking_momentum', signals: [{ source: 'attacking_momentum', intensity: 0.9, confidence: 0.7, experimental: true }] },
      }],
    },
  };
}

test('Key Moments uses the current server-validated YouTube ID with the logical video second', () => {
  const view = render(React.createElement(AggregateKeyMoments, {
    report: keyMomentReport(), video: { group_id: 'group-1', status: 'ready', artifact_url: '/logical.mp4' },
    externalVideo: { group_id: 'group-1', status: 'current', external_video: { provider: 'youtube', video_id: 'AbCdEfGhI_1', source_url: 'https://youtu.be/AbCdEfGhI_1', linked_video: { generation_id: 'g', input_semantic_digest: 'i', output_semantic_digest: 'o', timeline_span_sec: 900 }, updated_at: 'now' } },
    onSeekLocalVideo: () => assert.fail('current YouTube must be primary'),
  }));

  assert.ok(view.getByRole('heading', { name: 'Najważniejsze momenty' }));
  assert.ok(view.getByText('12:02'));
  assert.equal((view.getByRole('link', { name: 'Zobacz moment' }) as HTMLAnchorElement).getAttribute('href'), 'https://www.youtube.com/watch?v=AbCdEfGhI_1&t=722s');
  assert.equal(youtubeWatchUrl('AbCdEfGhI_1', -1.2), 'https://www.youtube.com/watch?v=AbCdEfGhI_1&t=0s');
});

test('Key Moments renders direct evidence metrics instead of the ranking score', () => {
  const momentum = keyMomentReport();
  const possession = keyMomentReport();
  possession.key_moments!.moments[0] = {
    ...possession.key_moments!.moments[0],
    moment_id: 'km-2',
    type: 'possession_dominance',
    importance_score: 0.48,
    headline: 'Wyraźna przewaga w rozpoznanym posiadaniu',
    evidence: { primary_signal: 'possession', signals: [{ source: 'possession', share_percent: 80, coverage: 0.8 }] },
  };
  momentum.key_moments!.moments[0].importance_score = 0.63;

  const view = render(React.createElement(React.Fragment, null,
    React.createElement(AggregateKeyMoments, { report: momentum, video: null, externalVideo: null, onSeekLocalVideo: () => undefined }),
    React.createElement(AggregateKeyMoments, { report: possession, video: null, externalVideo: null, onSeekLocalVideo: () => undefined }),
  ));

  assert.ok(view.getByText('Momentum: intensywność 90% · pewność 70% · eksperymentalne'));
  assert.ok(view.getByText('Rozpoznane posiadanie: 80% · pokrycie 80%'));
  assert.equal(view.queryByText(/Momentum 63%/), null);
  assert.equal(view.queryByText(/Rozpoznane posiadanie 48%/), null);
});

test('Key Moments never use stale YouTube and reuse the one ready local video seek action', () => {
  const seeks: number[] = [];
  const view = render(React.createElement(AggregateKeyMoments, {
    report: keyMomentReport(), video: { group_id: 'group-1', status: 'ready', artifact_url: '/logical.mp4' },
    externalVideo: { group_id: 'group-1', status: 'stale', external_video: { provider: 'youtube', video_id: 'AbCdEfGhI_1', source_url: 'https://youtu.be/AbCdEfGhI_1', linked_video: { generation_id: 'old', input_semantic_digest: 'i', output_semantic_digest: 'o', timeline_span_sec: 900 }, updated_at: 'now' } },
    onSeekLocalVideo: (timeSec) => seeks.push(timeSec),
  }));

  assert.equal(view.queryByRole('link', { name: 'Zobacz moment' }), null);
  fireEvent.click(view.getByRole('button', { name: 'Zobacz moment' }));
  assert.deepEqual(seeks, [722.5]);
  assert.equal(view.container.querySelectorAll('video').length, 0);
});

test('Key Moments keep visible timestamps without a current video action', () => {
  const view = render(React.createElement(AggregateKeyMoments, {
    report: keyMomentReport(), video: { group_id: 'group-1', status: 'not_generated' },
    externalVideo: { group_id: 'group-1', status: 'invalid' }, onSeekLocalVideo: () => assert.fail('no local video is ready'),
  }));

  assert.ok(view.getByText('12:02'));
  assert.equal(view.queryByRole('link', { name: 'Zobacz moment' }), null);
  assert.equal(view.queryByRole('button', { name: 'Zobacz moment' }), null);
});

test('aggregate page places Key Moments after the one local player and seeks it exactly when YouTube is unavailable', async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    const path = String(input);
    if (path.endsWith('/video')) return Response.json({ group_id: 'group-1', status: 'ready', artifact_url: '/logical.mp4' });
    return Response.json({ report: keyMomentReport(), validation: { status: 'compatible', blocking_reasons: [] }, external_video: { group_id: 'group-1', status: 'not_configured' } });
  };
  try {
    const view = render(React.createElement(MemoryRouter, { initialEntries: ['/published/match-groups/group-1/report'] }, React.createElement(Routes, null,
      React.createElement(Route, { path: '/published/match-groups/:groupId/report', element: React.createElement(AggregateMatchReportPage) }),
    )));
    await waitFor(() => assert.ok(view.getByRole('heading', { name: 'Najważniejsze momenty' })));
    const video = view.container.querySelector('video') as HTMLVideoElement;
    assert.ok(video);
    fireEvent.click(view.getByRole('button', { name: 'Zobacz moment' }));
    assert.equal(video.currentTime, 722.5);
    assert.equal(view.container.querySelectorAll('video').length, 1);
    assert.ok((view.getByRole('heading', { name: 'Najważniejsze momenty' }).compareDocumentPosition(view.getByRole('heading', { name: 'Podsumowanie drużyn' })) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0);
  } finally { globalThis.fetch = originalFetch; }
});
