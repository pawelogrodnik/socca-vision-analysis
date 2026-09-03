import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { PublicPlayerHeatmap } from '../src/components/PublicPlayerHeatmap.tsx';

const interactive = {
  method: 'pitch_meter_binned_canvas_heatmap_v1',
  width: 360,
  height: 720,
  grid_width: 48,
  grid_length: 96,
  radius: 14,
  max_value: 2,
  points: [{ x: 100, y: 300, value: 2 }],
};

test('heatmap renders an accessible average-position marker only when the coordinate is present', () => {
  const withAverage = renderToStaticMarkup(createElement(PublicPlayerHeatmap, {
    alt: 'Heatmapa Pawła',
    heatmap: { path: '', samples: 2, detected_samples: 2, quality: 'high', interactive, average_position: { pitch_m: [10, 20], x: 120, y: 300 } },
  }));
  const withoutAverage = renderToStaticMarkup(createElement(PublicPlayerHeatmap, {
    alt: 'Heatmapa Pawła',
    heatmap: { path: '', samples: 2, detected_samples: 2, quality: 'high', interactive },
  }));

  assert.match(withAverage, /średnia pozycja/);
  assert.match(withAverage, /cx="120"/);
  assert.doesNotMatch(withoutAverage, /public-heatmap-average-marker/);
});
