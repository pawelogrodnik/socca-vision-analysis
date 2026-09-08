import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { RedesignedPublishedReportContent } from '../src/components/RedesignedPublishedReportContent.tsx';
import { RedesignedReportVideoMoments } from '../src/components/RedesignedReportVideoMoments.tsx';
import { embedAtTimestamp } from '../src/components/RedesignedReportVideoMoments.tsx';
import {
  isPublishedReportId,
  playerComparableValue,
  publicReportInsights,
} from '../src/lib/redesignedPublicReportPresentation.ts';
import type { PublicMatchReport, PublicReportPlayer } from '../src/types.ts';

const report = {
  schema_version: 'public_match_report.v1',
  generated_at: '2026-09-08T12:00:00Z',
  id: 'published-merged-test',
  source_match_id: 'merged-test',
  report_type: 'public_match_report',
  match: { id: 'merged-test', title: 'Corgi vs Verisk', duration_sec: 2150 },
  teams: [
    { team_id: 'A', team_label: 'A', team_name: 'Corgi', playing_time_sec: 2150, total_distance_m: 102600, high_intensity_distance_m: 5000, sprint_count: 224, avg_speed_kmh: 4, peak_speed_kmh: 32.1, pass_candidates: 10, same_team_pass_candidates: 10, turnover_or_interception_candidates: 0, progressive_pass_candidates: 2, accepted_passes: 8, possession_share_percent: 57, pass_attempts: 100, completed_passes: 84, completion_rate: 84 },
    { team_id: 'B', team_label: 'B', team_name: 'Verisk', playing_time_sec: 2150, total_distance_m: 98700, high_intensity_distance_m: 4400, sprint_count: 187, avg_speed_kmh: 4, peak_speed_kmh: 30.4, pass_candidates: 10, same_team_pass_candidates: 10, turnover_or_interception_candidates: 0, progressive_pass_candidates: 1, accepted_passes: 7, possession_share_percent: 43, pass_attempts: 100, completed_passes: 78, completion_rate: 78 },
  ],
  players: [],
  key_moments: { schema_version: 'key_moments.v1', moments: [
    { moment_id: 'one', time_sec: 202, headline: 'Mocny pressing Corgi', origin: 'manual', public_category: 'other', note: 'Odbiór wysoko.' },
    { moment_id: 'two', time_sec: 940, headline: 'Szybka akcja Verisk', origin: 'manual', public_category: 'other' },
  ] },
  ball: { possession_timeline: [], attacking_momentum: { experimental: true, quality: 'high', warnings: [], timeline: [{ index: 0, minute: 5, label: '5–10 min', time_sec: 450, start_time_sec: 300, end_time_sec: 600, signed_score: 28, team_a_value: 28, team_b_value: 0, dominant_team_label: 'A' }] } },
} as PublicMatchReport;

test('route dispatch only selects the redesigned report for published ids', () => {
  assert.equal(isPublishedReportId('published-9c7485e4'), true);
  assert.equal(isPublishedReportId('published-merged-c33c30c0'), true);
  assert.equal(isPublishedReportId('9c7485e4'), false);
  assert.equal(isPublishedReportId(undefined), false);
});

test('redesigned report uses canonical facts without score, MVP, or half language', () => {
  const html = renderToStaticMarkup(createElement(RedesignedPublishedReportContent, {
    report,
    externalVideo: null,
    editorAllowed: false,
    onEditKeyMoments: () => undefined,
  }));
  assert.match(html, /Corgi vs Verisk/);
  assert.match(html, /Czas analizy: 35 min 50 s/);
  assert.match(html, /Najważniejsze momenty/);
  assert.doesNotMatch(html, /MVP|Wynik|pierwsza połowa|druga połowa|przerwa/i);
  assert.doesNotMatch(html, /Panel admin|Lista meczów|legacy/i);
});

test('current YouTube is embedded and every canonical moment stays in the scroll list', () => {
  const html = renderToStaticMarkup(createElement(RedesignedReportVideoMoments, {
    report,
    editorAllowed: false,
    onEditKeyMoments: () => undefined,
    externalVideo: { group_id: 'group', status: 'current', external_video: { provider: 'youtube', video_id: 'abc', source_url: 'https://youtube.com/watch?v=abc', embed_url: 'https://www.youtube.com/embed/abc', linked_video: { generation_id: 'g', input_semantic_digest: 'in', output_semantic_digest: 'out', timeline_span_sec: 2150 }, updated_at: '2026-09-08T12:00:00Z' } },
  }));
  assert.match(html, /youtube\.com\/embed\/abc/);
  assert.match(html, /Mocny pressing Corgi/);
  assert.match(html, /Szybka akcja Verisk/);
  assert.match(html, /Odtwórz/);
  assert.doesNotMatch(html, /Pokaż wszystkie|Zobacz wszystkie|lokalne wideo/i);
});

test('stale or absent YouTube does not introduce a local-video fallback', () => {
  const html = renderToStaticMarkup(createElement(RedesignedReportVideoMoments, {
    report,
    editorAllowed: false,
    onEditKeyMoments: () => undefined,
    externalVideo: { group_id: 'group', status: 'stale', external_video: null },
  }));
  assert.doesNotMatch(html, /<iframe|lokalne wideo|Otwórz lokalne/i);
  assert.match(html, /Mocny pressing Corgi/);
});

test('the moment editor action is absent normally and appears only when the backend allows dev editing', () => {
  const normal = renderToStaticMarkup(createElement(RedesignedReportVideoMoments, {
    report, editorAllowed: false, onEditKeyMoments: () => undefined, externalVideo: null,
  }));
  const allowed = renderToStaticMarkup(createElement(RedesignedReportVideoMoments, {
    report, editorAllowed: true, onEditKeyMoments: () => undefined, externalVideo: null,
  }));
  assert.doesNotMatch(normal, /Edytuj momenty/);
  assert.match(allowed, /Edytuj momenty/);
});

test('a moment click has a deterministic YouTube playback URL with the logical timestamp', () => {
  assert.equal(
    embedAtTimestamp('https://www.youtube.com/embed/abc?rel=0', 202.9),
    'https://www.youtube.com/embed/abc?rel=0&start=202&autoplay=1',
  );
});

test('insights stay deterministic and unavailable player rates remain unavailable', () => {
  const insights = publicReportInsights(report);
  assert.deepEqual(insights.map((item) => item.title), [
    'Corgi miało większe posiadanie',
    'Corgi miało wyższą skuteczność podań',
    'Największa intensywność: Corgi',
  ]);
  const unavailable = { player_id: 'p', player_name: 'P', playing_time_sec: 10, detected_time_sec: 10, total_distance_m: 4, avg_speed_kmh: 1, peak_speed_kmh: 2, high_intensity_distance_m: 0, sprint_count: 0, workload: { distance_per_5min_m: null } } as PublicReportPlayer;
  assert.equal(playerComparableValue(unavailable, 'distance_per_5min_m'), null);
  assert.equal(playerComparableValue({ ...unavailable, workload: { distance_per_5min_m: 0 } } as PublicReportPlayer, 'distance_per_5min_m'), 0);
});
