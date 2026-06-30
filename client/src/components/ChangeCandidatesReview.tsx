import { useEffect, useMemo, useState } from 'react';
import { getChangeCandidates, reviewChangeCandidates } from '../api';
import type {
  ChangeCandidate,
  ChangeCandidateReviewStatus,
  ChangeCandidateReviewUpdate,
  ChangeCandidatesDocument,
  Match
} from '../types';

const REVIEW_STATUSES: ChangeCandidateReviewStatus[] = [
  'needs_review',
  'confirmed',
  'uncertain',
  'rejected',
  'ignored'
];

const REVIEW_LABELS: Record<ChangeCandidateReviewStatus, string> = {
  needs_review: 'Do sprawdzenia',
  confirmed: 'Potwierdzona zmiana',
  uncertain: 'Niepewne',
  rejected: 'Odrzucone',
  ignored: 'Ignoruj'
};

type DraftReview = {
  review_status: ChangeCandidateReviewStatus;
  out_stable_subject_id: string;
  linked_existing_stable_subject_id: string;
  player_id: string;
  notes: string;
};

interface ChangeCandidatesReviewProps {
  match: Match;
  enabled: boolean;
}

export function ChangeCandidatesReview({ match, enabled }: ChangeCandidatesReviewProps) {
  const [document, setDocument] = useState<ChangeCandidatesDocument | null>(match.change_candidates || null);
  const [drafts, setDrafts] = useState<Record<string, DraftReview>>(() => buildDrafts(match.change_candidates));
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    let active = true;
    if (!enabled && !match.change_candidates) {
      setDocument(null);
      setDrafts({});
      return () => {
        active = false;
      };
    }
    setLoading(true);
    setError('');
    getChangeCandidates(match.id)
      .then((nextDocument) => {
        if (!active) return;
        setDocument(nextDocument);
        setDrafts(buildDrafts(nextDocument));
      })
      .catch((fetchError) => {
        if (!active) return;
        if (match.change_candidates) {
          setDocument(match.change_candidates);
          setDrafts(buildDrafts(match.change_candidates));
          return;
        }
        setError(fetchError instanceof Error ? fetchError.message : String(fetchError));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [enabled, match.id, match.change_candidates]);

  const candidates = document?.candidates || [];
  const summary = document?.summary || {};
  const reviewCounts = useMemo(() => {
    const counts = { confirmed: 0, ignored: 0, needs_review: 0, rejected: 0, uncertain: 0 };
    for (const candidate of candidates) {
      counts[normalizeStatus(candidate.review_status)] += 1;
    }
    return counts;
  }, [candidates]);

  function updateDraft(candidateId: string, patch: Partial<DraftReview>) {
    setDrafts((current) => ({
      ...current,
      [candidateId]: {
        review_status: current[candidateId]?.review_status || 'needs_review',
        out_stable_subject_id: current[candidateId]?.out_stable_subject_id || '',
        linked_existing_stable_subject_id: current[candidateId]?.linked_existing_stable_subject_id || '',
        player_id: current[candidateId]?.player_id || '',
        notes: current[candidateId]?.notes || '',
        ...patch
      }
    }));
  }

  async function saveReview() {
    if (!document) return;
    setSaving(true);
    setError('');
    setMessage('');
    const updates: ChangeCandidateReviewUpdate[] = document.candidates.map((candidate) => {
      const draft = drafts[candidate.candidate_id] || defaultDraft(candidate);
      return {
        candidate_id: candidate.candidate_id,
        review_status: draft.review_status,
        out_stable_subject_id: draft.out_stable_subject_id || null,
        linked_existing_stable_subject_id: draft.linked_existing_stable_subject_id || null,
        player_id: draft.player_id || null,
        notes: draft.notes
      };
    });
    try {
      const nextDocument = await reviewChangeCandidates(match.id, updates);
      setDocument(nextDocument);
      setDrafts(buildDrafts(nextDocument));
      setMessage('Zapisano review zmian.');
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
    <div className='artifact-box'>
      <div className='row between'>
        <div>
          <h3>Change candidates review</h3>
          <p className='muted'>
            Automatyczne kandydaty zmian: kto mogl zejsc, kto wszedl i czy nowy slot
            moze byc powrotem wczesniejszego zawodnika.
          </p>
        </div>
        <button type='button' onClick={saveReview} disabled={saving || loading || !document}>
          {saving ? 'Zapisywanie...' : 'Zapisz review zmian'}
        </button>
      </div>
      {loading && <p className='muted'>Ladowanie kandydatow zmian...</p>}
      {error && <p className='error'>{error}</p>}
      {message && <p className='success'>{message}</p>}
      {document && (
        <>
          <div className='chips'>
            <span>Kandydaci: {formatCount(summary.change_candidates ?? candidates.length)}</span>
            <span>Do sprawdzenia: {reviewCounts.needs_review}</span>
            <span>Confirmed: {reviewCounts.confirmed}</span>
            <span>Uncertain: {reviewCounts.uncertain}</span>
            <span>Rejected: {reviewCounts.rejected}</span>
            <span>Ignored: {reviewCounts.ignored}</span>
          </div>
          {candidates.length === 0 ? (
            <p className='muted'>
              Brak kandydatow zmian dla tego nagrania. To normalne dla krotkich sample bez zmian.
            </p>
          ) : (
            <div className='stats-table-wrap contact-review-table'>
              <table className='stats-table'>
                <thead>
                  <tr>
                    <th>Zmiana</th>
                    <th>Sugestia</th>
                    <th>Powrot / roster</th>
                    <th>Review</th>
                    <th>Notatka</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((candidate) => {
                    const draft = drafts[candidate.candidate_id] || defaultDraft(candidate);
                    const rosterPlayers = rosterPlayersForCandidate(match, candidate);
                    const outCandidates = candidate.out_candidates || [];
                    const reidCandidates = candidate.reid_candidates || [];
                    return (
                      <tr key={candidate.candidate_id}>
                        <td>
                          <strong>{candidate.candidate_id}</strong>
                          <span>{formatTeam(candidate.team_name, candidate.team_label)}</span>
                          <span>
                            t={formatSeconds(candidate.time_sec)} gap {formatSeconds(candidate.gap_sec)}
                          </span>
                          <span>
                            confidence {candidate.confidence || 'n/a'} {formatPercent(candidate.confidence_score)}
                          </span>
                        </td>
                        <td>
                          <strong>
                            {candidate.out_stable_player_id || 'out ?'} off -{' '}
                            {candidate.in_stable_player_id || 'in ?'} on
                          </strong>
                          <span>
                            out end {formatSeconds(candidate.out_end_time_sec)} / in start{' '}
                            {formatSeconds(candidate.in_start_time_sec)}
                          </span>
                          <select
                            value={draft.out_stable_subject_id}
                            onChange={(event) => updateDraft(candidate.candidate_id, { out_stable_subject_id: event.target.value })}
                          >
                            <option value=''>-- wybierz kto zszedl --</option>
                            {outCandidates.map((row) => (
                              <option key={recordText(row, 'stable_subject_id')} value={recordText(row, 'stable_subject_id')}>
                                {recordText(row, 'stable_player_id')} / gap {formatSeconds(recordNumber(row, 'gap_sec'))}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td>
                          <select
                            value={draft.linked_existing_stable_subject_id}
                            onChange={(event) => updateDraft(candidate.candidate_id, { linked_existing_stable_subject_id: event.target.value })}
                          >
                            <option value=''>Nowy anonimowy slot</option>
                            {reidCandidates.map((row) => (
                              <option key={recordText(row, 'stable_subject_id')} value={recordText(row, 'stable_subject_id')}>
                                Powrot {recordText(row, 'stable_player_id')} / score {formatPercent(recordNumber(row, 'score'))}
                              </option>
                            ))}
                          </select>
                          <select
                            value={draft.player_id}
                            onChange={(event) => updateDraft(candidate.candidate_id, { player_id: event.target.value })}
                          >
                            <option value=''>-- bez realnego zawodnika --</option>
                            {rosterPlayers.map((player) => (
                              <option key={player.id} value={player.id}>
                                {player.number ? `#${player.number} ` : ''}{player.name || player.id}
                              </option>
                            ))}
                          </select>
                          {candidate.suggested_real_player_name && (
                            <span>Sugestia: {candidate.suggested_real_player_name}</span>
                          )}
                        </td>
                        <td>
                          <select
                            value={draft.review_status}
                            onChange={(event) =>
                              updateDraft(candidate.candidate_id, {
                                review_status: normalizeStatus(event.target.value)
                              })
                            }
                          >
                            {REVIEW_STATUSES.map((status) => (
                              <option key={status} value={status}>
                                {REVIEW_LABELS[status]}
                              </option>
                            ))}
                          </select>
                          <span>{candidate.review_source || 'generated'}</span>
                        </td>
                        <td>
                          <textarea
                            rows={3}
                            value={draft.notes}
                            onChange={(event) => updateDraft(candidate.candidate_id, { notes: event.target.value })}
                            placeholder='Np. A08 to powrot A01 albo nowy zawodnik z lawki.'
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function buildDrafts(document?: ChangeCandidatesDocument | null): Record<string, DraftReview> {
  const drafts: Record<string, DraftReview> = {};
  for (const candidate of document?.candidates || []) {
    drafts[candidate.candidate_id] = defaultDraft(candidate);
  }
  return drafts;
}

function defaultDraft(candidate: ChangeCandidate): DraftReview {
  return {
    review_status: normalizeStatus(candidate.review_status),
    out_stable_subject_id: candidate.reviewed_out_stable_subject_id || candidate.out_stable_subject_id || '',
    linked_existing_stable_subject_id:
      candidate.linked_existing_stable_subject_id ||
      candidate.suggested_existing_stable_subject_id ||
      '',
    player_id: candidate.reviewed_player_id || candidate.suggested_real_player_id || '',
    notes: candidate.review_notes || ''
  };
}

function normalizeStatus(value: unknown): ChangeCandidateReviewStatus {
  const status = String(value || 'needs_review');
  return REVIEW_STATUSES.includes(status as ChangeCandidateReviewStatus)
    ? (status as ChangeCandidateReviewStatus)
    : 'needs_review';
}

function rosterPlayersForCandidate(match: Match, candidate: ChangeCandidate) {
  const candidateTeamId = candidate.team_id || '';
  const candidateTeamLabel = candidate.team_label || '';
  const team = (match.teams || []).find(
    (item) => item.id === candidateTeamId || item.name === candidate.team_name || item.id?.includes(candidateTeamLabel)
  );
  return team?.players || [];
}

function formatTeam(teamName: unknown, teamLabel: unknown): string {
  return `${teamName || 'Team'}${teamLabel ? ` (${teamLabel})` : ''}`;
}

function formatSeconds(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '--';
  }
  return `${numeric.toFixed(1)}s`;
}

function formatPercent(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '--';
  }
  return `${(numeric * 100).toFixed(0)}%`;
}

function formatCount(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return '--';
  }
  return String(Math.round(numeric));
}

function recordText(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return value == null ? '' : String(value);
}

function recordNumber(record: Record<string, unknown>, key: string): number {
  const value = Number(record[key]);
  return Number.isFinite(value) ? value : 0;
}
