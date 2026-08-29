import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { PublicPlayerWorkloadSection } from '../src/components/PublicPlayerWorkloadSection.tsx';
import {
  exactWindowLabel,
  formatHiRatio,
  formatRate,
  hasWorkloadMetrics,
  windowValue,
} from '../src/lib/publicPlayerWorkloadPresentation.ts';
import type { PublicReportPlayer } from '../src/types.ts';

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
