import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildTeamShapeTimeline,
  formatTeamShapeValue,
  shouldRenderTeamShape,
  TEAM_SHAPE_METRICS,
} from '../src/lib/teamShapePresentation.ts';
import type { TeamShapeDocument } from '../src/types.ts';


const shape: TeamShapeDocument = {
  available: true,
  teams: [
    {
      team_label: 'A',
      team_name: 'Corgi',
      timeline: [
        { label: '00:00', width_m: 20.5, depth_m: 18, compactness_m: 7, block_height_percent: 56.4 },
        { label: '01:00', width_m: null, depth_m: null, compactness_m: null, block_height_percent: null },
      ],
    },
    {
      team_label: 'B',
      team_name: 'Verisk',
      timeline: [{ label: '00:00', width_m: 18, depth_m: 20, compactness_m: 6, block_height_percent: 43 }],
    },
  ],
};

test('shape visibility requires an available two-team document', () => {
  assert.equal(shouldRenderTeamShape(shape), true);
  assert.equal(shouldRenderTeamShape(undefined), false);
  assert.equal(shouldRenderTeamShape({ available: false, teams: shape.teams }), false);
});

test('coach metrics use Polish labels without technical vocabulary', () => {
  const labels = TEAM_SHAPE_METRICS.map((metric) => metric.label);
  assert.deepEqual(labels, ['Szerokość', 'Długość ustawienia', 'Zwartość', 'Wysokość ustawienia']);
  assert.equal(labels.includes('Block height'), false);
  assert.equal(labels.includes('Próbki'), false);
});

test('height uses percent and spatial distances use meters', () => {
  assert.equal(formatTeamShapeValue(56.4, 'block_height_percent'), '56%');
  assert.equal(formatTeamShapeValue(20.5, 'width_m'), '20,5 m');
});

test('timeline selection keeps insufficient bins missing instead of zero', () => {
  const width = buildTeamShapeTimeline(shape, 'width_m');
  const height = buildTeamShapeTimeline(shape, 'block_height_percent');
  assert.equal(width[0].team_A, 20.5);
  assert.equal(width[1].team_A, null);
  assert.equal(height[0].team_A, 56.4);
});

test('all chart metric selections use their matching timeline values', () => {
  for (const metric of TEAM_SHAPE_METRICS) {
    const timeline = buildTeamShapeTimeline(shape, metric.key);
    assert.equal(timeline[0].team_A, shape.teams?.[0].timeline?.[0][metric.key]);
    assert.equal(timeline[1].team_A, null);
  }
});