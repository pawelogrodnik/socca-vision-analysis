import assert from 'node:assert/strict';
import test from 'node:test';

import { buildMatchPhasePayload, matchPhaseFormValues } from '../src/utils/matchPhaseConfig.ts';

const direction = 'towards_y_min';

test('builds a two-half payload with a separate halftime gap', () => {
  const result = buildMatchPhasePayload({ firstHalfEnd: '1200', secondHalfStart: '1500', teamADirection: direction });

  assert.deepEqual(result, {
    payload: {
      first_half_end_time_sec: 1200,
      second_half_start_time_sec: 1500,
      team_a_first_half_direction: direction,
    },
    error: null,
  });
});

test('allows equal half boundaries when the recording has no halftime gap', () => {
  const result = buildMatchPhasePayload({ firstHalfEnd: '1200', secondHalfStart: '1200', teamADirection: direction });

  assert.equal(result.error, null);
});

test('rejects reversed half boundaries', () => {
  const result = buildMatchPhasePayload({ firstHalfEnd: '1500', secondHalfStart: '1200', teamADirection: direction });

  assert.match(result.error || '', /nie może być później/);
  assert.equal(result.payload, null);
});

test('rejects invalid or incomplete period values', () => {
  assert.match(
    buildMatchPhasePayload({ firstHalfEnd: 'abc', secondHalfStart: '1500', teamADirection: direction }).error || '',
    /nieujemną liczbą sekund/,
  );
  assert.match(
    buildMatchPhasePayload({ firstHalfEnd: '-1', secondHalfStart: '1500', teamADirection: direction }).error || '',
    /nieujemną liczbą sekund/,
  );
  assert.match(
    buildMatchPhasePayload({ firstHalfEnd: '', secondHalfStart: '1500', teamADirection: direction }).error || '',
    /podaj koniec pierwszej i początek drugiej połowy/,
  );
});

test('reads persisted half boundaries from the canonical periods', () => {
  const values = matchPhaseFormValues({
    periods: [
      {
        period_id: 'first_half',
        start_time_sec: 0,
        end_time_sec: 1200,
        team_attack_directions: { A: direction, B: 'towards_y_max' },
      },
      {
        period_id: 'second_half',
        start_time_sec: 1500,
        end_time_sec: 2700,
        team_attack_directions: { A: 'towards_y_max', B: direction },
      },
    ],
  });

  assert.deepEqual(values, { firstHalfEnd: '1200', secondHalfStart: '1500', teamADirection: direction });
});