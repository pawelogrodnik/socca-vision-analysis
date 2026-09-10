import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { comparisonBarWidth, RedesignedPublishedReportContent, redesignedPossessionDataKeys } from '../src/components/RedesignedPublishedReportContent.tsx';
import { RedesignedReportVideoMoments } from '../src/components/RedesignedReportVideoMoments.tsx';
import { youtubePlayerEmbedUrl } from '../src/components/RedesignedReportVideoMoments.tsx';
import { PublicPlayerWorkloadSection } from '../src/components/PublicPlayerWorkloadSection.tsx';
import { isGoalkeeperWorkloadRow, redesignedWorkloadHue } from '../src/components/PublicPlayerWorkloadSection.tsx';
import {
  balancedPossessionPercentages,
  isPublishedReportId,
  momentumDisplayBuckets,
  playerComparableValue,
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
  ball: { possession_timeline: [{ index: 0, minute: 5, label: '5', start_time_sec: 0, end_time_sec: 300, team_a_frames: 57, team_b_frames: 43, known_team_frames: 100, cumulative_team_a_frames: 57, cumulative_team_b_frames: 43, cumulative_known_team_frames: 100, cumulative_team_a_percent: 57, cumulative_team_b_percent: 43, free_frames: 0, unknown_frames: 0, team_a_share: 0.57, team_b_share: 0.43, controlled_coverage: 1, controlled_coverage_percent: 100, unknown_coverage: 0 }], attacking_momentum: { experimental: true, quality: 'high', warnings: [], timeline: [{ index: 0, minute: 5, label: '5–10 min', time_sec: 450, start_time_sec: 300, end_time_sec: 600, signed_score: 28, team_a_value: 28, team_b_value: 0, dominant_team_label: 'A' }] } },
} as PublicMatchReport;

const player = {
  player_id: 'kowalski', player_name: 'Kowalski', team_id: 'A', team_name: 'Corgi', playing_time_sec: 300, detected_time_sec: 300,
  total_distance_m: 520, avg_speed_kmh: 4, peak_speed_kmh: 23, high_intensity_distance_m: 88, sprint_count: 2,
  workload: { semantics: 'reviewed', rate_window_sec: 300, minimum_rate_sample_sec: 120, detected_time_sec: 300, distance_per_5min_m: 520, high_intensity_distance_per_5min_m: 88, sprints_per_5min: 2, high_intensity_distance_ratio: 0.17, activity_windows: [{ window_index: 0, start_time_sec: 0, end_time_sec: 300, duration_sec: 300, display_label: '0–5', detected_time_sec: 300, total_distance_m: 520, high_intensity_distance_m: 88, sprint_count: 2, rate_status: 'reportable', distance_per_5min_m: 520, high_intensity_distance_per_5min_m: 88, sprints_per_5min: 2 }], best_activity_window: null },
  heatmap: { path: 'published/matches/published-merged-test/heatmaps/kowalski.png', samples: 1, detected_samples: 1, quality: 'available', interactive: { method: 'grid', width: 360, height: 720, grid_width: 10, grid_length: 20, radius: 12, max_value: 1, points: [{ x: 120, y: 250, value: 1 }] } },
} as PublicReportPlayer;

const goalkeeper = {
  ...player,
  player_id: 'keeper',
  player_name: 'Goalkeeper',
  player_role: 'goalkeeper',
  heatmap: { ...player.heatmap!, path: 'published/matches/published-merged-test/heatmaps/keeper.png' },
} as PublicReportPlayer;

const reportWithPlayer = { ...report, players: [player, goalkeeper] } as PublicMatchReport;

test('route dispatch only selects the redesigned report for published ids', () => {
  assert.equal(isPublishedReportId('published-9c7485e4'), true);
  assert.equal(isPublishedReportId('published-merged-c33c30c0'), true);
  assert.equal(isPublishedReportId('9c7485e4'), false);
  assert.equal(isPublishedReportId(undefined), false);
});

test('redesigned report removes repeated summaries and uses canonical facts without score, MVP, half, or provenance language', () => {
  const html = renderToStaticMarkup(createElement(RedesignedPublishedReportContent, {
    report: reportWithPlayer,
    externalVideo: null,
    editorAllowed: false,
    onEditKeyMoments: () => undefined,
  }));
  assert.match(html, /Corgi vs Verisk/);
  assert.match(html, /Czas analizy: 35 min 50 s/);
  assert.match(html, /Najważniejsze momenty/);
  assert.doesNotMatch(html, /Szczegółowa analiza drużyn, zawodników i przebiegu dostępnego materiału/);
  assert.doesNotMatch(html, /Szybkie podsumowanie|Najważniejsze wnioski|Scalony mecz|fragmentów/);
  assert.doesNotMatch(html, /MVP|Wynik|pierwsza połowa|druga połowa|przerwa/i);
  assert.doesNotMatch(html, /Panel admin|Lista meczów|legacy/i);
});

test('match flow reads the real cumulative possession contract and uses diverging momentum bars', () => {
  const html = renderToStaticMarkup(createElement(RedesignedPublishedReportContent, {
    report: reportWithPlayer, externalVideo: null, editorAllowed: false, onEditKeyMoments: () => undefined,
  }));
  assert.match(html, /data-chart-kind="possession-area"/);
  assert.match(html, /data-chart-kind="diverging-momentum"/);
  assert.deepEqual(redesignedPossessionDataKeys, { teamA: 'cumulative_team_a_percent', teamB: 'cumulative_team_b_percent' });
  assert.match(html, /Porównanie drużyn/);
  assert.match(html, /57%/);
  assert.match(html, /102\.6 km/);
  assert.match(html, /redesign-comparison-bar left/);
  const attempts = html.indexOf('Próby podań');
  const completed = html.indexOf('Podania celne');
  const completion = html.indexOf('Skuteczność podań');
  assert.ok(attempts < completed && completed < completion);
});

test('momentum display buckets average five-second samples into readable signed one-minute bars', () => {
  const buckets = momentumDisplayBuckets([
    { time_sec: 5, signed_score: 20 },
    { time_sec: 25, signed_score: 40 },
    { time_sec: 59, signed_score: -30 },
    { time_sec: 61, signed_score: -18 },
    { time_sec: 119, signed_score: -42 },
  ]);
  assert.deepEqual(buckets, [
    { start_time_sec: 0, end_time_sec: 60, signed_score: 10 },
    { start_time_sec: 60, end_time_sec: 120, signed_score: -30 },
  ]);
});

test('percentage comparison bars retain their natural zero-to-one-hundred scale', () => {
  assert.equal(comparisonBarWidth(38, 100), 38);
  assert.equal(comparisonBarWidth(63, 100), 63);
  assert.equal(comparisonBarWidth(44, 100), 44);
  assert.equal(comparisonBarWidth(48, 100), 48);
});

test('displayed possession shares use one balanced rounding and always total one hundred percent', () => {
  assert.deepEqual(balancedPossessionPercentages(38, 63), { left: 38, right: 62 });
  assert.deepEqual(balancedPossessionPercentages(57, 43), { left: 57, right: 43 });
  assert.equal(balancedPossessionPercentages(null, 43), null);
});

test('players appear before canonical activity and heatmaps in the redesigned section order', () => {
  const html = renderToStaticMarkup(createElement(RedesignedPublishedReportContent, {
    report: reportWithPlayer, externalVideo: null, editorAllowed: false, onEditKeyMoments: () => undefined,
  }));
  const players = html.indexOf('Statystyki zawodników');
  const activity = html.indexOf('Aktywność w 5-minutowych oknach');
  const heatmaps = html.indexOf('Heatmapy zawodników');
  assert.ok(players >= 0 && players < activity && activity < heatmaps);
  assert.match(html, /redesign-workload-legend/);
  assert.match(html, /Heatmapa Kowalski/);
  assert.match(html, /Heatmapa Goalkeeper/);
  assert.doesNotMatch(html, /Wybór heatmapy zawodnika/);
});

test('redesigned workload keeps goalkeepers visible but gray and outside the intensity scale', () => {
  const html = renderToStaticMarkup(createElement(PublicPlayerWorkloadSection, {
    players: [player, goalkeeper],
    variant: 'redesigned',
  }));
  assert.match(html, /Kowalski/);
  assert.match(html, /Goalkeeper/);
  assert.match(html, /workload-goalkeeper-row/);
  assert.match(html, /workload-goalkeeper-cell/);
  assert.match(html, /Bramkarz — pominięty w skali intensywności/);
  assert.ok(html.indexOf('Kowalski') < html.indexOf('Goalkeeper'));
  assert.doesNotMatch(html, /Macierz pokazuje kolejne pięciominutowe|Sprint jest liczony/);
  assert.match(html, /redesign-workload-legend/);
  assert.equal(isGoalkeeperWorkloadRow(goalkeeper), true);
  assert.equal(isGoalkeeperWorkloadRow({ ...player, player_name: 'Mati GK' }), true);
});

test('redesigned workload palette maps lower measured activity to red/orange and higher activity to green', () => {
  assert.equal(redesignedWorkloadHue(0), 0);
  assert.ok(redesignedWorkloadHue(0.35) > 45);
  assert.ok(redesignedWorkloadHue(0.65) > 95);
  assert.equal(redesignedWorkloadHue(1), 150);
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

test('an operator-approved stale YouTube link remains embedded', () => {
  const html = renderToStaticMarkup(createElement(RedesignedReportVideoMoments, {
    report,
    editorAllowed: false,
    onEditKeyMoments: () => undefined,
    externalVideo: { group_id: 'group', status: 'stale', external_video: { provider: 'youtube', video_id: 'abc', source_url: 'https://youtube.com/watch?v=abc', embed_url: 'https://www.youtube.com/embed/abc', linked_video: { generation_id: 'old', input_semantic_digest: 'in', output_semantic_digest: 'out', timeline_span_sec: 2150 }, updated_at: '2026-09-08T12:00:00Z' } },
  }));
  assert.match(html, /youtube\.com\/embed\/abc/);
  assert.doesNotMatch(html, /lokalne wideo|Otwórz lokalne/i);
  assert.match(html, /Mocny pressing Corgi/);
});

test('an approved YouTube video keeps the core module visible when no moments are published yet', () => {
  const html = renderToStaticMarkup(createElement(RedesignedReportVideoMoments, {
    report: { ...report, key_moments: { ...report.key_moments!, moments: [] } },
    editorAllowed: false,
    onEditKeyMoments: () => undefined,
    externalVideo: { group_id: 'group', status: 'current', external_video: { provider: 'youtube', video_id: 'abc', source_url: 'https://youtube.com/watch?v=abc', embed_url: 'https://www.youtube-nocookie.com/embed/abc', linked_video: { generation_id: 'g', input_semantic_digest: 'in', output_semantic_digest: 'out', timeline_span_sec: 2150 }, updated_at: '2026-09-08T12:00:00Z' } },
  }));
  assert.match(html, /youtube-nocookie\.com\/embed\/abc/);
  assert.match(html, /Brak opublikowanych momentów/);
});

test('video section shows a small status while the saved YouTube link is loading', () => {
  const html = renderToStaticMarkup(createElement(RedesignedReportVideoMoments, {
    report: { ...report, key_moments: undefined },
    editorAllowed: false,
    externalVideo: null,
    externalVideoLoading: true,
    onEditKeyMoments: () => undefined,
  }));
  assert.match(html, /Sprawdzam zapisane wideo YouTube…/);
  assert.match(html, /role="status"/);
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

test('the persistent no-cookie player URL enables the YouTube JavaScript API', () => {
  assert.equal(
    youtubePlayerEmbedUrl('https://www.youtube-nocookie.com/embed/abc?rel=0', 'https://socca.example'),
    'https://www.youtube-nocookie.com/embed/abc?rel=0&enablejsapi=1&origin=https%3A%2F%2Fsocca.example',
  );
});

test('unavailable player rates remain unavailable while measured zero remains a real value', () => {
  const unavailable = { player_id: 'p', player_name: 'P', playing_time_sec: 10, detected_time_sec: 10, total_distance_m: 4, avg_speed_kmh: 1, peak_speed_kmh: 2, high_intensity_distance_m: 0, sprint_count: 0, workload: { distance_per_5min_m: null } } as PublicReportPlayer;
  assert.equal(playerComparableValue(unavailable, 'distance_per_5min_m'), null);
  assert.equal(playerComparableValue({ ...unavailable, workload: { distance_per_5min_m: 0 } } as PublicReportPlayer, 'distance_per_5min_m'), 0);
});

test('redesigned heatmaps prefer canonical published PNGs and never render the canvas instead', () => {
  const html = renderToStaticMarkup(createElement(RedesignedPublishedReportContent, {
    report: reportWithPlayer, externalVideo: null, editorAllowed: false, onEditKeyMoments: () => undefined,
  }));
  assert.match(html, /Heatmapa Kowalski/);
  assert.match(html, /Heatmapa Goalkeeper/);
  assert.match(html, /published-merged-test\/heatmaps\/kowalski\.png/);
  assert.match(html, /published-merged-test\/heatmaps\/keeper\.png/);
  assert.doesNotMatch(html, /public-heatmap-canvas/);
  assert.doesNotMatch(html, /Wybór heatmapy zawodnika/);
});

test('redesigned report renders Team Shape only when the canonical report carries it', () => {
  const withoutShape = renderToStaticMarkup(createElement(RedesignedPublishedReportContent, {
    report: reportWithPlayer, externalVideo: null, editorAllowed: false, onEditKeyMoments: () => undefined,
  }));
  assert.doesNotMatch(withoutShape, /Ustawienie drużyn/);

  const teamShape = {
    available: true,
    scope: 'all_in_play',
    pitch_dimensions_m: { width_m: 30, length_m: 47.4 },
    teams: ['A', 'B'].map((label) => ({
      team_label: label,
      team_name: label === 'A' ? 'Corgi' : 'Verisk',
      summary: { average_width_m: 20.5, average_depth_m: 18, average_compactness_m: 7, average_block_height_percent: 56.4 },
      average_shape: { grid: { columns: 6, rows: 10 }, cells: [{ column: 2, row: 7, value: 0.38 }] },
      timeline: [{ label: '00:00', width_m: 20.5, depth_m: 18, compactness_m: 7, block_height_percent: 56.4 }],
    })),
    takeaways: [],
  };
  const withShape = renderToStaticMarkup(createElement(RedesignedPublishedReportContent, {
    report: { ...reportWithPlayer, team_shape: teamShape } as PublicMatchReport,
    externalVideo: null, editorAllowed: false, onEditKeyMoments: () => undefined,
  }));
  assert.match(withShape, /Ustawienie drużyn/);
  assert.ok(withShape.indexOf('Heatmapy zawodników') < withShape.indexOf('Ustawienie drużyn'));
});
