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

test('published presentation prefers the canonical PNG over the interactive canvas', () => {
  const html = renderToStaticMarkup(createElement(PublicPlayerHeatmap, {
    alt: 'Heatmapa Pawła',
    fallbackSrc: '/published/matches/published-merged-x/heatmaps/pawel.png',
    heatmap: { path: 'published/matches/published-merged-x/heatmaps/pawel.png', samples: 2, detected_samples: 2, quality: 'high', interactive, average_position: { pitch_m: [10, 20], x: 120, y: 300 } },
    presentation: 'published',
  }));

  assert.match(html, /<img/);
  assert.match(html, /published-merged-x\/heatmaps\/pawel\.png/);
  assert.doesNotMatch(html, /<canvas/);
  assert.match(html, /public-heatmap-published-marker/);
  assert.match(html, /średnia pozycja/);
});

test('published presentation keeps the interactive canvas as fallback when no PNG exists', () => {
  const html = renderToStaticMarkup(createElement(PublicPlayerHeatmap, {
    alt: 'Heatmapa Pawła',
    heatmap: { path: '', samples: 2, detected_samples: 2, quality: 'high', interactive, average_position: { pitch_m: [10, 20], x: 120, y: 300 } },
    presentation: 'published',
  }));

  assert.match(html, /<canvas/);
  assert.doesNotMatch(html, /<img/);
  assert.match(html, /cx="120"/);
});

test('default presentation keeps the legacy interactive-first behavior unchanged', () => {
  const html = renderToStaticMarkup(createElement(PublicPlayerHeatmap, {
    alt: 'Heatmapa Pawła',
    fallbackSrc: '/published/matches/published-merged-x/heatmaps/pawel.png',
    heatmap: { path: 'published/matches/published-merged-x/heatmaps/pawel.png', samples: 2, detected_samples: 2, quality: 'high', interactive },
  }));

  assert.match(html, /<canvas/);
  assert.doesNotMatch(html, /<img/);
  assert.doesNotMatch(html, /public-heatmap-published/);
});
