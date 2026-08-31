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
  hasReportablePlayerChartMetric,
  hasWorkloadMetrics,
  playerChartEmptyMessage,
  visiblePlayerChartMetric,
  windowValue,
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
  assert.equal(windowValue(window, 'sprints'), '0');
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

test('activity matrix renders actual windows, a valid zero sprint and safety copy', () => {
  const html = renderToStaticMarkup(createElement(PublicPlayerWorkloadSection, { players: [player], teamName: 'Corgi' }));

  assert.match(html, /Aktywność w 5-minutowych oknach/);
  assert.match(html, /Dystans/);
  assert.match(html, /0–5/);
  assert.match(html, /35–36/);
  assert.match(html, /412 m/);
  assert.match(html, /dostępnego nagrania/);
  assert.match(html, /nie próbuje sztucznie odtwarzać brakujących minut/);
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
