import { useMemo } from 'react';
import type { Match, PublishedMatchDetail } from '../types';
import { pretty } from '../lib/helpers';

interface MatchSummaryProps {
  match: Match;
}

export function MatchSummary({ match }: MatchSummaryProps) {
  const playerCount = useMemo(
    () =>
      (match.teams || []).reduce(
        (sum, team) => sum + (team.players?.length || 0),
        0,
      ),
    [match.teams],
  );

  return (
    <div className='summary'>
      <h3>{match.title}</h3>
      <p className='muted'>
        {match.match_date || 'brak daty'} · {match.season || 'brak sezonu'} ·{' '}
        {match.venue || 'brak miejsca'}
      </p>
      <div className='chips'>
        <span>Status: {match.status || 'uploaded'}</span>
        <span>Format: {match.format || '7v7'}</span>
        <span>Drużyny: {(match.teams || []).length}</span>
        <span>Zawodnicy: {playerCount}</span>
        {match.published_match_id && (
          <span>DB: {match.published_match_id}</span>
        )}
      </div>
      {(match.teams || []).map((team) => (
        <div className='team-row' key={team.id || team.name}>
          <span
            className='color-dot'
            style={{ background: team.color || '#64748b' }}
          />
          <strong>{team.name}</strong>
          <span className='muted'>{team.players?.length || 0} zawodników</span>
        </div>
      ))}
    </div>
  );
}

interface PublishedMatchSummaryProps {
  match: PublishedMatchDetail;
}

export function PublishedMatchSummary({ match }: PublishedMatchSummaryProps) {
  const source = match.package?.match;

  return (
    <div className='summary'>
      <h3>{match.title}</h3>
      <p className='muted'>
        {match.match_date || 'brak daty'} · {match.season || 'brak sezonu'} ·{' '}
        {match.venue || 'brak miejsca'}
      </p>
      <div className='chips'>
        <span>Status: {match.status}</span>
        <span>Drużyny: {match.team_count}</span>
        <span>Zawodnicy: {match.player_count}</span>
        <span>Tracki: {match.tracks_count ?? 0}</span>
        <span>Klatki: {match.frames_processed ?? 0}</span>
        <span>Warnings: {match.warnings_count}</span>
      </div>
      {(source?.teams || []).map((team) => (
        <div className='team-row' key={team.id || team.name}>
          <span
            className='color-dot'
            style={{ background: team.color || '#64748b' }}
          />
          <strong>{team.name}</strong>
          <span className='muted'>{team.players?.length || 0} zawodników</span>
        </div>
      ))}
      {match.package?.player_assignments?.summary && (
        <>
          <h4>Zaakceptowane przypisania</h4>
          <div className='chips'>
            <span>
              Raw tracklety:{' '}
              {match.package.player_assignments.summary.raw_tracklets}
            </span>
            <span>
              Przypisane tracklety:{' '}
              {match.package.player_assignments.summary.assigned_tracklets}
            </span>
            <span>
              Nieprzypisane:{' '}
              {match.package.player_assignments.summary.unassigned_tracklets}
            </span>
          </div>
        </>
      )}
      {match.package?.stable_players?.summary && (
        <>
          <h4>Stabilni zawodnicy</h4>
          <div className='chips'>
            <span>
              Stable players: {match.package.stable_players.summary.stable_players}
            </span>
            <span>
              Ryzykowne merge: {match.package.stable_players.summary.risky_links}
            </span>
            <span>
              Low confidence:{' '}
              {match.package.stable_players.summary.low_confidence_players}
            </span>
          </div>
        </>
      )}
      <h4>Analysis snapshot</h4>
      <pre>
        {pretty(
          match.package?.analysis_report || { status: 'no analysis report' },
        )}
      </pre>
    </div>
  );
}
