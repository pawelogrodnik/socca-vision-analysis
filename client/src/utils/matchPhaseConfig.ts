import type { MatchPhaseConfigDocument, MatchPhaseConfigPayload } from '../types';

export type MatchPhaseFormValues = {
  firstHalfEnd: string;
  secondHalfStart: string;
  teamADirection: string;
};

export type MatchPhasePayloadResult =
  | { payload: MatchPhaseConfigPayload; error: null }
  | { payload: null; error: string };

export function matchPhaseFormValues(document: MatchPhaseConfigDocument): MatchPhaseFormValues {
  const firstHalf = document.periods.find((period) => period.period_id === 'first_half');
  const secondHalf = document.periods.find((period) => period.period_id === 'second_half');
  return {
    firstHalfEnd: firstHalf?.end_time_sec != null ? String(firstHalf.end_time_sec) : '',
    secondHalfStart:
      secondHalf?.start_time_sec != null
        ? String(secondHalf.start_time_sec)
        : document.second_half_start_time_sec != null
          ? String(document.second_half_start_time_sec)
          : '',
    teamADirection:
      document.default_team_a_first_half_direction
      || document.periods[0]?.team_attack_directions?.A
      || 'towards_y_min',
  };
}

export function buildMatchPhasePayload(values: MatchPhaseFormValues): MatchPhasePayloadResult {
  const firstHalfEnd = parseOptionalSeconds(values.firstHalfEnd, 'Koniec pierwszej połowy');
  if (firstHalfEnd.error) return { payload: null, error: firstHalfEnd.error };
  const secondHalfStart = parseOptionalSeconds(values.secondHalfStart, 'Początek drugiej połowy');
  if (secondHalfStart.error) return { payload: null, error: secondHalfStart.error };

  if ((firstHalfEnd.value == null) !== (secondHalfStart.value == null)) {
    return { payload: null, error: 'Dla meczu z dwiema połowami podaj koniec pierwszej i początek drugiej połowy.' };
  }
  if (
    firstHalfEnd.value != null
    && secondHalfStart.value != null
    && firstHalfEnd.value > secondHalfStart.value
  ) {
    return { payload: null, error: 'Koniec pierwszej połowy nie może być później niż początek drugiej połowy.' };
  }
  return {
    payload: {
      first_half_end_time_sec: firstHalfEnd.value,
      second_half_start_time_sec: secondHalfStart.value,
      team_a_first_half_direction: values.teamADirection,
    },
    error: null,
  };
}

function parseOptionalSeconds(rawValue: string, label: string): { value: number | null; error: string | null } {
  const value = rawValue.trim();
  if (!value) return { value: null, error: null };
  const seconds = Number(value);
  if (!Number.isFinite(seconds) || seconds < 0) {
    return { value: null, error: `${label} musi być nieujemną liczbą sekund.` };
  }
  return { value: seconds, error: null };
}