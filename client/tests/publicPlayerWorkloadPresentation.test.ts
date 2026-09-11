import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';

import { PublicPlayerWorkloadSection } from '../src/components/PublicPlayerWorkloadSection.tsx';
import { PublicPlayerStatsSection } from '../src/components/PublicPlayerStatsSection.tsx';
import {
  exactWindowLabel,
  formatHiRatio,
  formatRate,
  hasMeasuredDistanceInWindow,
  hasReportablePlayerChartMetric,
  hasWorkloadMetrics,
  isUnavailableWorkloadCell,
  metricWindowMaximum,
  metricWindowRange,
  playerChartEmptyMessage,
  visiblePlayerChartMetric,
  windowIntensity,
  windowRelativeIntensity,
  windowValue,
  workloadCellTooltip,
  workloadPresentationMode,
} from '../src/lib/publicPlayerWorkloadPresentation.ts';
import type { PublicReportPlayer } from '../src/types.ts';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
Object.defineProperty(globalThis, 'window', { configurable: true, value: dom.window });
Object.defineProperty(globalThis, 'document', { configurable: true, value: dom.window.document });
Object.defineProperty(globalThis, 'navigator', { configurable: true, value: dom.window.navigator });
Object.defineProperty(globalThis, 'HTMLElement', { configurable: true, value: dom.window.HTMLElement });
Object.defineProperty(globalThis, 'Node', { configurable: true, value: dom.window.Node });
Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true, writable: true });

const { act, cleanup, fireEvent, render } = await import('@testing-library/react');

afterEach(() => cleanup());

const window = {
  window_index: 7,
  start_time_sec: 2100,
  end_time_sec: 2172,
  duration_sec: 72,
  display_label: '35–36',
  detected_time_sec: 52,
  total_distance_m: 74,
  high_intensity_distance_m: 11,
  sprint_count: 0,
  rate_status: 'insufficient_detected_sample',
  distance_per_5min_m: null,
  high_intensity_distance_per_5min_m: null,
  sprints_per_5min: null,
} as const;

const player = {
  player_id: 'pawel',
  player_name: 'Paweł',
  team_name: 'Corgi',
  workload: {
    semantics: 'reviewed_confirmed_detected_in_play',
    rate_window_sec: 300,
    minimum_rate_sample_sec: 120,
    detected_time_sec: 600,
    distance_per_5min_m: 512.4,
    high_intensity_distance_per_5min_m: 93.2,
    sprints_per_5min: 0,
    high_intensity_distance_ratio: 0.218,
    activity_windows: [
      { ...window, window_index: 0, start_time_sec: 0, end_time_sec: 300, duration_sec: 300, display_label: '0–5', detected_time_sec: 260, total_distance_m: 412, high_intensity_distance_m: 83, sprint_count: 0, rate_status: 'reportable', distance_per_5min_m: 475.4, high_intensity_distance_per_5min_m: 95.8, sprints_per_5min: 0 },
      window,
    ],
    best_activity_window: { window_index: 0, display_label: '0–5', start_time_sec: 0, end_time_sec: 300, detected_time_sec: 260, total_distance_m: 412, distance_per_5min_m: 475.4, high_intensity_distance_m: 83, sprint_count: 0 },
  },
} as PublicReportPlayer;

test('workload presentation preserves null versus valid zero and final partial windows', () => {
  assert.equal(hasWorkloadMetrics([player]), true);
  assert.equal(hasWorkloadMetrics([{ ...player, workload: undefined }]), false);
  assert.equal(formatRate(null, 'm'), '—');
  assert.equal(formatRate(512.4, 'm'), '512 m / 5 min');
  assert.equal(formatRate(0, 'sprints'), '0.0 / 5 min');
  assert.equal(formatHiRatio(0.218), '22%');
  assert.equal(exactWindowLabel(window), '35:00–36:12');
  assert.equal(windowValue(window, 'sprints', 'normalized'), '—');
  assert.equal(windowValue(player.workload!.activity_windows[0], 'distancePerMinute', 'normalized'), '95 m/min');
});

test('hidden sprint selection falls back to the available distance metric', () => {
  assert.equal(
    visiblePlayerChartMetric('sprintsPer5', ['minutes', 'distancePer5', 'distanceKm']),
    'distancePer5',
  );
  assert.equal(
    visiblePlayerChartMetric('sprintsPer5', ['minutes', 'distanceKm']),
    'distanceKm',
  );
});

test('normalized player-chart metric availability is specific to the selected metric', () => {
  const unavailable = {
    ...player,
    workload: {
      ...player.workload,
      distance_per_5min_m: null,
      high_intensity_distance_per_5min_m: null,
      sprints_per_5min: null,
    },
  } as PublicReportPlayer;
  const zeroSprint = {
    ...unavailable,
    workload: { ...unavailable.workload!, sprints_per_5min: 0 },
  } as PublicReportPlayer;

  assert.equal(hasReportablePlayerChartMetric([unavailable], 'distancePer5'), false);
  assert.equal(hasReportablePlayerChartMetric([zeroSprint], 'sprintsPer5'), false);
  assert.equal(
    hasReportablePlayerChartMetric([
      { ...zeroSprint, workload: { ...zeroSprint.workload!, sprints_per_5min: 0.1 } },
    ], 'sprintsPer5'),
    true,
  );
  assert.equal(hasReportablePlayerChartMetric([unavailable, player], 'highIntensityPer5'), true);
  assert.equal(hasReportablePlayerChartMetric([unavailable, { ...unavailable }], 'highIntensityPer5'), false);
  assert.equal(
    playerChartEmptyMessage('distancePer5'),
    'Brak wystarczającego czasu wykrytego do obliczenia tej metryki.',
  );
  assert.equal(
    playerChartEmptyMessage('minutes'),
    'Brak rozpoznanych z imienia zawodników tej drużyny.',
  );
});

test('activity matrix defaults to the understandable average distance per minute', () => {
  const html = renderToStaticMarkup(createElement(PublicPlayerWorkloadSection, { players: [player], teamName: 'Corgi' }));

  assert.match(html, /Aktywność w 5-minutowych oknach/);
  assert.match(html, /Dystans/);
  assert.match(html, /Śr\. dystans \/ min/);
  assert.ok(html.indexOf('Dystans') < html.indexOf('Śr. dystans / min'));
  assert.ok(html.indexOf('Śr. dystans / min') < html.indexOf('Czas wykryty'));
  assert.match(html, /0–5/);
  assert.doesNotMatch(html, /35–36/);
  assert.match(html, /95 m\/min/);
  assert.doesNotMatch(html, />412 m</);
  assert.match(html, /dostępnego nagrania/);
  assert.match(html, /nie próbuje sztucznie odtwarzać brakujących minut/);
});

test('canonical activity matrix preserves reportable partial samples, unavailable evidence, and a valid zero', async () => {
  const reportablePartial = {
    ...player,
    workload: {
      ...player.workload!,
      activity_windows: [
        {
          ...player.workload!.activity_windows[0],
          detected_time_sec: 150,
          total_distance_m: 274,
          distance_per_5min_m: 548,
          high_intensity_distance_m: 42,
          high_intensity_distance_per_5min_m: 84,
          sprint_count: 0,
          sprints_per_5min: 0,
        },
        { ...window, detected_time_sec: 58, total_distance_m: 9000 },
      ],
    },
  } as PublicReportPlayer;
  const view = render(createElement(PublicPlayerWorkloadSection, { players: [reportablePartial], teamName: 'Corgi' }));

  assert.equal(workloadPresentationMode([reportablePartial]), 'normalized');
  assert.equal(metricWindowMaximum([reportablePartial], 'distance', 'normalized'), 9000);
  assert.ok(windowIntensity(window, 'distance', 9000, 'normalized') > 0);
  assert.equal(isUnavailableWorkloadCell(window, 'distance', 'normalized'), false);
  assert.match(view.container.innerHTML, />110 m\/min</);
  assert.doesNotMatch(view.container.innerHTML, /35:00–36:12/);
  assert.equal(hasMeasuredDistanceInWindow([reportablePartial], 7, 'normalized'), false);

  const averageDistanceCell = view.getByLabelText(/Paweł, 0:00–5:00 materiału/);
  assert.match(averageDistanceCell.getAttribute('title') || '', /Śr\. dystans \/ min: 110 m\/min/);
  assert.match(averageDistanceCell.getAttribute('title') || '', /Czas wykryty: 2:30/);
  assert.equal(view.queryByLabelText(/Paweł, 35:00–36:12 materiału/), null);

  await act(async () => {
    fireEvent.click(view.getByRole('button', { name: 'Dystans' }));
  });
  assert.match(view.container.innerHTML, />274 m</);
  const distanceCell = view.getByLabelText(/Paweł, 0:00–5:00 materiału/);
  assert.match(distanceCell.getAttribute('title') || '', /Dystans: 274 m/);

  await act(async () => {
    fireEvent.click(view.getByRole('button', { name: 'Speed bursts / 5 min' }));
  });
  assert.match(view.container.innerHTML, />0\.0</);
});

test('redesigned workload intensity spans the measured range instead of grouping all high values as green', () => {
  const lower = {
    ...player,
    player_id: 'lower',
    workload: {
      ...player.workload!,
      activity_windows: [{ ...player.workload!.activity_windows[0], total_distance_m: 300 }],
    },
  } as PublicReportPlayer;
  const higher = {
    ...player,
    player_id: 'higher',
    workload: {
      ...player.workload!,
      activity_windows: [{ ...player.workload!.activity_windows[0], total_distance_m: 600 }],
    },
  } as PublicReportPlayer;
  const middle = {
    ...player,
    player_id: 'middle',
    workload: { ...player.workload!, activity_windows: [player.workload!.activity_windows[0]] },
  } as PublicReportPlayer;
  const range = metricWindowRange([lower, middle, higher], 'distance', 'normalized');

  assert.deepEqual(range, { minimum: 300, maximum: 600 });
  assert.equal(windowRelativeIntensity(lower.workload!.activity_windows[0], 'distance', range, 'normalized'), 0);
  assert.ok(windowRelativeIntensity(middle.workload!.activity_windows[0], 'distance', range, 'normalized') > 0.3);
  assert.equal(windowRelativeIntensity(higher.workload!.activity_windows[0], 'distance', range, 'normalized'), 1);
});

test('HI, sprints, and detected time use their canonical semantics', async () => {
  const view = render(createElement(PublicPlayerWorkloadSection, { players: [player], teamName: 'Corgi' }));

  await act(async () => {
    fireEvent.click(view.getByRole('button', { name: 'HI / 5 min' }));
  });
  assert.match(view.container.innerHTML, />96 m</);

  await act(async () => {
    fireEvent.click(view.getByRole('button', { name: 'Speed bursts / 5 min' }));
  });
  assert.equal(
    windowValue({ ...player.workload!.activity_windows[0], sprint_count: 1, sprints_per_5min: 3.5 }, 'sprints', 'normalized'),
    '3.5',
  );
  assert.match(view.container.innerHTML, />0\.0</);

  await act(async () => {
    fireEvent.click(view.getByRole('button', { name: 'Czas wykryty' }));
  });
  assert.match(view.container.innerHTML, />4:20</);
  const detectedCell = view.getByLabelText(/Paweł, 0:00–5:00 materiału/);
  assert.match(detectedCell.getAttribute('title') || '', /potwierdzony czas obserwacji/);
  assert.doesNotMatch(detectedCell.getAttribute('title') || '', /czas gry: [0-9]/);
});

test('legacy workload payload keeps a distance-per-minute default without guessing normalized rates', () => {
  const legacyPlayer = {
    ...player,
    workload: {
      ...player.workload!,
      activity_windows: player.workload!.activity_windows.map(({ rate_status, distance_per_5min_m, high_intensity_distance_per_5min_m, sprints_per_5min, ...legacy }) => legacy),
    },
  } as PublicReportPlayer;
  const html = renderToStaticMarkup(createElement(PublicPlayerWorkloadSection, { players: [legacyPlayer], teamName: 'Corgi' }));

  assert.equal(workloadPresentationMode([legacyPlayer]), 'legacy');
  assert.match(html, /Dystans/);
  assert.doesNotMatch(html, /Dystans \/ 5 min/);
  assert.match(html, />95 m\/min</);
});

test('physical and merged workload rows share one canonical presentation path', () => {
  const mergedPlayer = { ...player, player_id: 'merged-pawel', player_name: 'Paweł · scalony' } as PublicReportPlayer;
  assert.equal(windowValue(player.workload!.activity_windows[0], 'distance', workloadPresentationMode([player])), '412 m');
  assert.equal(windowValue(mergedPlayer.workload!.activity_windows[0], 'distance', workloadPresentationMode([mergedPlayer])), '412 m');
  assert.equal(
    workloadCellTooltip('Paweł', player.workload!.activity_windows[1], player.workload!.activity_windows[1], 'distance', 'normalized'),
    workloadCellTooltip('Paweł', mergedPlayer.workload!.activity_windows[1], mergedPlayer.workload!.activity_windows[1], 'distance', 'normalized'),
  );
});

test('workload table labels Max sprint as a validated speed and keeps zero as unavailable', async () => {
  const zeroSprintPlayer = {
    ...player,
    max_sprint_speed_kmh: 0,
  } as PublicReportPlayer;
  const view = render(
    createElement(PublicPlayerStatsSection, { players: [zeroSprintPlayer], teamName: 'Corgi' }),
  );

  await act(async () => {
    fireEvent.click(view.getByRole('button', { name: 'Obciążenie' }));
  });

  assert.match(view.container.innerHTML, /Najwyższa wiarygodna prędkość utrzymana podczas zaakceptowanego sprintu/);
  assert.match(view.container.innerHTML, /Max sprint/);
  assert.match(view.container.innerHTML, /—/);
});

test('legacy player data without workload keeps the existing basic stats section', () => {
  const legacyPlayer = { ...player, workload: undefined } as PublicReportPlayer;
  const html = renderToStaticMarkup(
    createElement(PublicPlayerStatsSection, { players: [legacyPlayer], teamName: 'Corgi' }),
  );

  assert.match(html, /Statystyki rozpoznanych zawodników/);
  assert.match(html, /Paweł/);
  assert.doesNotMatch(html, /Obciążenie/);
});
