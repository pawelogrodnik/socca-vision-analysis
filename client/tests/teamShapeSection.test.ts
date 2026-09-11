import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { densityVisualLevel, TeamShapeSection } from '../src/components/TeamShapeSection.tsx';
import type { PublicReportTeam, TeamShapeDocument } from '../src/types.ts';


const reportTeams: PublicReportTeam[] = [
  { team_label: 'A', team_name: 'Corgi', display_color: '#ef4444' },
  { team_label: 'B', team_name: 'Verisk', display_color: '#22c55e' },
];

const teamShape: TeamShapeDocument = {
  available: true,
  scope: 'all_in_play',
  pitch_dimensions_m: { width_m: 30, length_m: 47.4 },
  teams: [
    {
      team_label: 'A',
      team_name: 'Corgi',
      summary: {
        average_width_m: 20.5,
        average_depth_m: 18,
        average_compactness_m: 7,
        average_block_height_percent: 56.4,
      },
      average_shape: {
        grid: { columns: 6, rows: 10 },
        cells: [{ column: 2, row: 7, value: 0.38 }],
      },
      timeline: [
        { label: '00:00', width_m: 20.5, depth_m: 18, compactness_m: 7, block_height_percent: 56.4 },
        { label: '01:00', width_m: null, depth_m: null, compactness_m: null, block_height_percent: null },
      ],
    },
    {
      team_label: 'B',
      team_name: 'Verisk',
      summary: {
        average_width_m: 18,
        average_depth_m: 20,
        average_compactness_m: 6,
        average_block_height_percent: 43,
      },
      average_shape: {
        grid: { columns: 6, rows: 10 },
        cells: [{ column: 3, row: 6, value: 0.42 }],
      },
      timeline: [{ label: '00:00', width_m: 18, depth_m: 20, compactness_m: 6, block_height_percent: 43 }],
    },
  ],
  takeaways: ['Corgi grał średnio o 2.5 m szerzej niż Verisk.'],
};

test('available Team Shape renders unchanged coach-facing metrics and semantically labelled density pitches', () => {
  const html = renderToStaticMarkup(createElement(TeamShapeSection, { teamShape, reportTeams }));

  assert.match(html, /Ustawienie drużyn/);
  assert.match(html, /Szerokość/);
  assert.match(html, /Długość ustawienia/);
  assert.match(html, /Zwartość/);
  assert.match(html, /Wysokość ustawienia/);
  assert.match(html, /20,5 m/);
  assert.match(html, /56%/);
  assert.match(html, /Corgi/);
  assert.match(html, /Verisk/);
  assert.match(html, /Najczęściej zajmowane strefy/);
  assert.match(html, /Jaśniejszy obszar oznacza strefę/);
  assert.match(html, /Intensywność jest normalizowana osobno dla każdej drużyny/);
  assert.match(html, /Kierunek ataku ↑/);
  assert.match(html, /team-shape-density-map/);
  assert.match(html, /density-level-5/);
  assert.doesNotMatch(html, /Średnie ustawienie|Gęstość Corgi/);
  assert.match(html, /Zmiany ustawienia w czasie/);
  assert.match(html, /Wybierz metrykę dla wykresu poniżej/);
  assert.match(html, /Metryka wykresu zmian ustawienia/);
  assert.doesNotMatch(html, /Próbki|Block height|diagnostics|readiness|sample_count/);
});

test('density visual levels keep near-zero zones quiet and reserve the strongest tone for peaks', () => {
  assert.equal(densityVisualLevel(0, 1), 0);
  assert.equal(densityVisualLevel(0.05, 1), 0);
  assert.equal(densityVisualLevel(0.06, 1), 1);
  assert.equal(densityVisualLevel(0.22, 1), 2);
  assert.equal(densityVisualLevel(0.45, 1), 3);
  assert.equal(densityVisualLevel(0.7, 1), 4);
  assert.equal(densityVisualLevel(0.88, 1), 5);
  assert.equal(densityVisualLevel(1, 1), 5);
});

test('missing or unavailable Team Shape renders no section', () => {
  assert.equal(renderToStaticMarkup(createElement(TeamShapeSection, { reportTeams })), '');
  assert.equal(
    renderToStaticMarkup(
      createElement(TeamShapeSection, { teamShape: { ...teamShape, available: false }, reportTeams }),
    ),
    '',
  );
});

test('an older Team Shape payload without density cells stays readable', () => {
  const legacy = {
    ...teamShape,
    teams: teamShape.teams?.map((team) => ({
      ...team,
      average_shape: { grid: { columns: 6, rows: 10 } },
    })),
  } as TeamShapeDocument;
  const html = renderToStaticMarkup(createElement(TeamShapeSection, { teamShape: legacy, reportTeams }));
  assert.match(html, /Ustawienie drużyn/);
  assert.match(html, /20,5 m/);
  assert.doesNotMatch(html, /team-shape-density-map/);
});
