import { useEffect, useState } from 'react';
import { getMatchPhaseConfig, saveMatchPhaseConfig } from '../api';
import type { Match, MatchPhaseConfigDocument } from '../types';
import { buildMatchPhasePayload, matchPhaseFormValues } from '../utils/matchPhaseConfig';

interface MatchPhaseConfigPanelProps {
  match: Match;
  enabled: boolean;
}

const TEAM_A_DIRECTIONS = [
  { value: 'towards_y_min', label: 'Team A first half: towards top/y min' },
  { value: 'towards_y_max', label: 'Team A first half: towards bottom/y max' },
  { value: 'towards_x_min', label: 'Team A first half: towards left/x min' },
  { value: 'towards_x_max', label: 'Team A first half: towards right/x max' }
];

export function MatchPhaseConfigPanel({ match, enabled }: MatchPhaseConfigPanelProps) {
  const [document, setDocument] = useState<MatchPhaseConfigDocument | null>(match.match_phase_config || null);
  const [firstHalfEnd, setFirstHalfEnd] = useState('');
  const [secondHalfStart, setSecondHalfStart] = useState('');
  const [teamADirection, setTeamADirection] = useState('towards_y_min');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    setDocument(match.match_phase_config || null);
  }, [match.match_phase_config]);

  useEffect(() => {
    if (!document) return;
    const values = matchPhaseFormValues(document);
    setFirstHalfEnd(values.firstHalfEnd);
    setSecondHalfStart(values.secondHalfStart);
    setTeamADirection(values.teamADirection);
  }, [document]);

  useEffect(() => {
    let active = true;
    if (!enabled && !match.match_phase_config) {
      return () => {
        active = false;
      };
    }
    setLoading(true);
    setError('');
    getMatchPhaseConfig(match.id)
      .then((nextDocument) => {
        if (!active) return;
        setDocument(nextDocument);
      })
      .catch((fetchError) => {
        if (!active) return;
        if (!match.match_phase_config) {
          setError(fetchError instanceof Error ? fetchError.message : String(fetchError));
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [enabled, match.id, match.match_phase_config]);

  async function save() {
    setSaving(true);
    setError('');
    setMessage('');
    const result = buildMatchPhasePayload({ firstHalfEnd, secondHalfStart, teamADirection });
    if (result.payload === null) {
      setSaving(false);
      setError(result.error);
      return;
    }
    try {
      const nextDocument = await saveMatchPhaseConfig(match.id, result.payload);
      setDocument(nextDocument);
      setMessage('Zapisano fazy meczu i odświeżono zależne analizy.');
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : String(saveError));
    } finally {
      setSaving(false);
    }
  }

  if (!enabled && !document) {
    return null;
  }

  return (
    <div className='quality-alert'>
      <div className='row between'>
        <div>
          <strong>Match phase / attack direction</strong>
          <span>
            Ustaw koniec pierwszej i początek drugiej połowy, aby pominąć przerwę. Kierunek jest używany w analizie ustawienia drużyn i statystykach kandydatów.
          </span>
        </div>
        <button type='button' onClick={save} disabled={saving || loading}>
          {saving ? 'Zapisywanie...' : 'Zapisz fazy'}
        </button>
      </div>
      {loading && <span>Ladowanie konfiguracji faz...</span>}
      {error && <span className='error'>{error}</span>}
      {message && <span className='success'>{message}</span>}
      <div className='row'>
        <label>
          Koniec pierwszej połowy (sek.)
          <input
            type='number'
            min='0'
            step='0.1'
            value={firstHalfEnd}
            onChange={(event) => setFirstHalfEnd(event.target.value)}
            placeholder='np. 1200'
          />
        </label>
        <label>
          Początek drugiej połowy (sek.)
          <input
            type='number'
            min='0'
            step='0.1'
            value={secondHalfStart}
            onChange={(event) => setSecondHalfStart(event.target.value)}
            placeholder='np. 1500'
          />
        </label>
        <label>
          Kierunek Team A w pierwszej polowie
          <select value={teamADirection} onChange={(event) => setTeamADirection(event.target.value)}>
            {TEAM_A_DIRECTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      {document && (
        <div className='chips'>
          <span>Periods: {document.periods.length}</span>
          <span>Second half: {document.second_half_start_time_sec ?? 'not set'}</span>
          {document.periods.map((period) => (
            <span key={period.period_id}>
              {period.period_id}: A {period.team_attack_directions?.A || 'unknown'} / B {period.team_attack_directions?.B || 'unknown'}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
