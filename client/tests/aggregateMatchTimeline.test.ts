import assert from 'node:assert/strict';
import { test } from 'node:test';

import { projectAggregateTimelineIntervals } from '../src/components/AggregateMatchTimeline.tsx';

test('possession projection closes the final server interval at its end time', () => {
  const projected = projectAggregateTimelineIntervals([
    { start_time_sec: 0, end_time_sec: 60, values: { corgi: 60 } },
    { start_time_sec: 60, end_time_sec: 120, values: { corgi: 40 } },
  ], ['corgi']);

  assert.deepEqual(projected, [
    { time_sec: 0, corgi: 60 },
    { time_sec: 60, corgi: 40 },
    { time_sec: 120, corgi: 40 },
  ]);
});

test('aggregate possession keeps unequal logical timestamps numeric', () => {
  const projected = projectAggregateTimelineIntervals([
    { start_time_sec: 0, end_time_sec: 60, values: { corgi: 60 } },
    { start_time_sec: 60, end_time_sec: 83, values: { corgi: 50 } },
    { start_time_sec: 83, end_time_sec: 143, values: { corgi: 40 } },
  ], ['corgi']);

  assert.deepEqual(projected.map((point) => point.time_sec), [0, 60, 83, 143]);
});

test('momentum projection closes the final rebased server point at its end time', () => {
  const projected = projectAggregateTimelineIntervals([
    { start_time_sec: 0, end_time_sec: 60, values: { corgi: 1.5, verisk: -1.5 } },
    { start_time_sec: 60, end_time_sec: 120, values: { corgi: 0.75, verisk: -0.75 } },
  ], ['corgi', 'verisk']);

  assert.deepEqual(projected.at(-1), { time_sec: 120, corgi: 0.75, verisk: -0.75 });
});
