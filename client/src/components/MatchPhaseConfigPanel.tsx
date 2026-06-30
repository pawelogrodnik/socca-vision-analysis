import { useEffect, useState } from 'react';
import { getMatchPhaseConfig, saveMatchPhaseConfig } from '../api';
import type { Match, MatchPhaseConfigDocument } from '../types';

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
    setSecondHalfStart(document.second_half_start_time_sec != null ? String(document.second_half_start_time_sec) : '');
    setTeamADirection(document.default_team_a_first_half_direction || firstTeamADirection(document) || 'towards_y_min');
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
    const trimmed = secondHalfStart.trim();
    const parsedSecondHalfStart = Number(trimmed);
    if (trimmed && !Number.isFinite(parsedSecondHalfStart)) {
      setSaving(false);
      setError('Start drugiej polowy musi byc liczba sekund.');
      return;
    }
    const payload = trimmed
      ? {
          second_half_start_time_sec: parsedSecondHalfStart,
          team_a_first_half_direction: teamADirection
        }
      : {
          second_half_start_time_sec: null,
          team_a_first_half_direction: teamADirection
        };
    try {
      const nextDocument = await saveMatchPhaseConfig(match.id, payload);
      setDocument(nextDocument);
      setMessage('Zapisano fazy meczu i odswiezono pass_candidates.json.');
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
            Ustaw poczatek drugiej polowy, jesli video zawiera zmiane stron. Kierunek jest uzywany tylko w candidate stats.
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
          Start drugiej polowy (sek.)
          <input
            type='number'
            min='0'
            step='0.1'
            value={secondHalfStart}
            onChange={(event) => setSecondHalfStart(event.target.value)}
            placeholder='np. 2700'
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

function firstTeamADirection(document: MatchPhaseConfigDocument): string | null {
  const first = document.periods[0];
  return first?.team_attack_directions?.A || null;
}
