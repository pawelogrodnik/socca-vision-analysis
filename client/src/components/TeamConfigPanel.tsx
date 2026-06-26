import { useEffect, useState } from 'react';
import { getTeamConfig, reviewTeamConfig } from '../api';
import type {
  Match,
  Team,
  TeamConfigDocument,
  TeamConfigReviewState,
  TeamConfigReviewPayload,
} from '../types';
import { errorMessage } from '../lib/helpers';

interface TeamConfigPanelProps {
  match: Match;
  onStatus: (message: string) => void;
  onSaved: () => Promise<void> | void;
}

type TeamConfigRow = TeamConfigReviewPayload['teams'][number];

function labelForIndex(index: number): 'A' | 'B' {
  return index === 0 ? 'A' : 'B';
}

function optionValue(team: Team, index: number): string {
  return `${team.id || ''}|${team.name}|${team.color || ''}|${labelForIndex(index)}`;
}

function rowFromConfig(config: TeamConfigDocument): TeamConfigRow[] {
  return config.teams
    .filter((team) => team.team_label === 'A' || team.team_label === 'B')
    .map((team) => ({
      team_label: team.team_label as 'A' | 'B',
      team_id: team.team_id ?? null,
      team_name: team.team_name,
      display_color: team.display_color ?? null,
      detected_color_hex: team.detected_color_hex ?? null,
      locked: team.locked,
      notes: team.notes || '',
      goalkeeper_exceptions: team.goalkeeper_exceptions || [],
    }));
}

function teamStat(stats: TeamConfigReviewState | null, label: string, key: string): string {
  const row = stats?.team_stats?.teams.find((item) => item.team_label === label);
  const value = row?.[key];
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(1);
  return value == null ? 'n/a' : String(value);
}

function teamPeakSpeed(stats: TeamConfigReviewState | null, label: string): string {
  const row = stats?.team_stats?.teams.find((item) => item.team_label === label);
  const value = row?.peak_sustained_speed_kmh ?? row?.top_speed_kmh;
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(1);
  return value == null ? 'n/a' : String(value);
}

function updateRow(rows: TeamConfigRow[], label: 'A' | 'B', patch: Partial<TeamConfigRow>): TeamConfigRow[] {
  return rows.map((row) => (row.team_label === label ? { ...row, ...patch } : row));
}

export function TeamConfigPanel({ match, onStatus, onSaved }: TeamConfigPanelProps) {
  const [review, setReview] = useState<TeamConfigReviewState | null>(null);
  const [rows, setRows] = useState<TeamConfigRow[]>([]);
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const data = await getTeamConfig(match.id);
      setReview(data);
      setRows(rowFromConfig(data.team_config));
      onStatus(`Zaladowano team config: ${data.team_config.locked ? 'locked' : 'unlocked'}.`);
    } catch (error) {
      setReview(null);
      setRows([]);
      onStatus(`Brak team config: ${errorMessage(error)}`);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (match.analysis_report?.status === 'completed') {
      load().catch((error) => onStatus(errorMessage(error)));
    } else {
      setReview(null);
      setRows([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match.id, match.analysis_report?.status]);

  async function save() {
    const updated = await reviewTeamConfig(match.id, { teams: rows });
    setReview(updated);
    setRows(rowFromConfig(updated.team_config));
    await onSaved();
    onStatus('Zapisano team config i przeliczono team/player stats.');
  }

  if (match.analysis_report?.status !== 'completed') {
    return null;
  }

  if (!review) {
    return (
      <section className='card'>
        <div className='row between'>
          <div>
            <h2>Team config</h2>
            <p className='muted'>Uruchom analize, zeby utworzyc team_config.json.</p>
          </div>
          <button type='button' onClick={load} disabled={loading}>
            Odswiez
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className='card'>
      <div className='row between'>
        <div>
          <h2>Team config i team stats</h2>
          <p className='muted'>
            Team A/B jest glownym kontraktem dla statystyk. Kolor koszulek jest tylko automatycznym sygnalem
            pomocniczym; po review trzymamy stabilny label A/B.
          </p>
        </div>
        <div className='row'>
          <button type='button' onClick={load} disabled={loading}>
            Odswiez
          </button>
          <button type='button' onClick={save}>
            Zapisz team config
          </button>
        </div>
      </div>

      <div className='chips'>
        <span>Method: {review.team_config.team_clusters_method || 'n/a'}</span>
        <span>Locked: {review.team_config.locked ? 'yes' : 'no'}</span>
        <span>Unknown stable: {review.team_config.unknown_stable_players ?? 0}</span>
        <span>Team stats scope: {review.team_stats?.scope || 'n/a'}</span>
      </div>

      <div className='grid two compact'>
        {rows.map((row) => {
          const config = review.team_config.teams.find((team) => team.team_label === row.team_label);
          return (
            <div className='team-card' key={row.team_label}>
              <div className='row between'>
                <h3>Team {row.team_label}</h3>
                <label className='inline-check'>
                  <input
                    type='checkbox'
                    checked={Boolean(row.locked)}
                    onChange={(event) =>
                      setRows((current) => updateRow(current, row.team_label, { locked: event.target.checked }))
                    }
                  />
                  Lock
                </label>
              </div>

              <label>
                Roster team
                <select
                  value={
                    match.teams.find((team) => team.id === row.team_id || team.name === row.team_name)
                      ? optionValue(
                          match.teams.find((team) => team.id === row.team_id || team.name === row.team_name) as Team,
                          match.teams.findIndex((team) => team.id === row.team_id || team.name === row.team_name),
                        )
                      : ''
                  }
                  onChange={(event) => {
                    const [team_id, team_name, color] = event.target.value.split('|');
                    setRows((current) =>
                      updateRow(current, row.team_label, {
                        team_id: team_id || null,
                        team_name: team_name || `Team ${row.team_label}`,
                        display_color: color || row.display_color || null,
                      }),
                    );
                  }}
                >
                  <option value=''>Manual / unknown</option>
                  {match.teams.slice(0, 2).map((team, index) => (
                    <option value={optionValue(team, index)} key={team.id || team.name}>
                      {team.name}
                    </option>
                  ))}
                </select>
              </label>

              <div className='grid two compact'>
                <label>
                  Name
                  <input
                    value={row.team_name || ''}
                    onChange={(event) =>
                      setRows((current) => updateRow(current, row.team_label, { team_name: event.target.value }))
                    }
                  />
                </label>
                <label>
                  Display color
                  <input
                    type='color'
                    value={row.display_color || '#94a3b8'}
                    onChange={(event) =>
                      setRows((current) => updateRow(current, row.team_label, { display_color: event.target.value }))
                    }
                  />
                </label>
              </div>

              <div className='chips'>
                <span>Stable slots: {config?.stable_players_count ?? 0}</span>
                <span>Players: {teamStat(review, row.team_label, 'players')}</span>
                <span>Distance: {teamStat(review, row.team_label, 'total_distance_m')} m</span>
                <span>Peak sustained: {teamPeakSpeed(review, row.team_label)} km/h</span>
              </div>

              <details className='debug-details'>
                <summary>Auto team evidence</summary>
                <div className='chips'>
                  <span>Detected color: {config?.detected_color_hex || 'n/a'}</span>
                  <span>Cluster confidence: {config?.cluster_confidence ?? 'n/a'}</span>
                </div>
              </details>

              <label>
                Notes
                <textarea
                  rows={2}
                  value={row.notes || ''}
                  onChange={(event) =>
                    setRows((current) => updateRow(current, row.team_label, { notes: event.target.value }))
                  }
                />
              </label>
            </div>
          );
        })}
      </div>
    </section>
  );
}
