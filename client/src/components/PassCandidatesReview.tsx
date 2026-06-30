import { useEffect, useMemo, useState } from 'react';
import { getPassCandidates, reviewPassCandidates } from '../api';
import type {
  Match,
  PassCandidateReviewStatus,
  PassCandidateReviewUpdate,
  PassCandidatesDocument
} from '../types';

const REVIEW_STATUSES: PassCandidateReviewStatus[] = [
  'needs_review',
  'accepted',
  'uncertain',
  'rejected'
];

const REVIEW_LABELS: Record<PassCandidateReviewStatus, string> = {
  needs_review: 'Do sprawdzenia',
  accepted: 'Prawdziwe podanie',
  uncertain: 'Niepewne',
  rejected: 'Odrzucone'
};

type DraftReview = {
  review_status: PassCandidateReviewStatus;
  notes: string;
};

interface PassCandidatesReviewProps {
  match: Match;
  enabled: boolean;
}

export function PassCandidatesReview({ match, enabled }: PassCandidatesReviewProps) {
  const [document, setDocument] = useState<PassCandidatesDocument | null>(match.pass_candidates || null);
  const [drafts, setDrafts] = useState<Record<string, DraftReview>>(() => buildDrafts(match.pass_candidates));
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    let active = true;
    if (!enabled && !match.pass_candidates) {
      setDocument(null);
      setDrafts({});
      return () => {
        active = false;
      };
    }
    setLoading(true);
    setError('');
    getPassCandidates(match.id)
      .then((nextDocument) => {
        if (!active) return;
        setDocument(nextDocument);
        setDrafts(buildDrafts(nextDocument));
      })
      .catch((fetchError) => {
        if (!active) return;
        if (match.pass_candidates) {
          setDocument(match.pass_candidates);
          setDrafts(buildDrafts(match.pass_candidates));
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
  }, [enabled, match.id, match.pass_candidates]);

  const candidates = document?.candidates || [];
  const summary = document?.summary || {};
  const reviewCounts = useMemo(() => {
    const counts = { accepted: 0, needs_review: 0, rejected: 0, uncertain: 0 };
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
    const updates: PassCandidateReviewUpdate[] = document.candidates.map((candidate) => {
      const draft = drafts[candidate.candidate_id] || {
        review_status: normalizeStatus(candidate.review_status),
        notes: candidate.review_notes || ''
      };
      return {
        candidate_id: candidate.candidate_id,
        review_status: draft.review_status,
        notes: draft.notes
      };
    });
    try {
      const nextDocument = await reviewPassCandidates(match.id, updates);
      setDocument(nextDocument);
      setDrafts(buildDrafts(nextDocument));
      setMessage('Zapisano review kandydatow podan i odswiezono pass_review_report.json.');
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
    <div className='contact-review-panel'>
      <div className='row between'>
        <div>
          <h4>Pass candidates review</h4>
          <p className='muted'>
            Zatwierdzaj tylko kandydaty, ktore wizualnie wygladaja jak realne podanie.
          </p>
        </div>
        <button type='button' onClick={saveReview} disabled={saving || loading || !document}>
          {saving ? 'Zapisywanie...' : 'Zapisz pass review'}
        </button>
      </div>
      {loading && <p className='muted'>Ladowanie kandydatow podan...</p>}
      {error && <p className='error'>{error}</p>}
      {message && <p className='success'>{message}</p>}
      {document && (
        <>
          <div className='chips'>
            <span>Kandydaci: {formatCount(summary.pass_candidates ?? candidates.length)}</span>
            <span>Do sprawdzenia: {reviewCounts.needs_review}</span>
            <span>Accepted: {reviewCounts.accepted}</span>
            <span>Uncertain: {reviewCounts.uncertain}</span>
            <span>Rejected: {reviewCounts.rejected}</span>
            <span>Final passes: {formatCount(summary.final_stat_passes)}</span>
            <span>Final forward: {formatCount(summary.final_forward_passes)}</span>
            <span>Final progressive: {formatCount(summary.final_progressive_passes)}</span>
          </div>
          {candidates.length === 0 ? (
            <p className='muted'>Brak kandydatow podan dla tego runu.</p>
          ) : (
            <div className='stats-table-wrap contact-review-table'>
              <table className='stats-table'>
                <thead>
                  <tr>
                    <th>Kandydat</th>
                    <th>Od-do</th>
                    <th>Zakres</th>
                    <th>Geometria</th>
                    <th>Review</th>
                    <th>Notatka</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((candidate) => {
                    const draft = drafts[candidate.candidate_id] || {
                      review_status: normalizeStatus(candidate.review_status),
                      notes: candidate.review_notes || ''
                    };
                    return (
                      <tr key={candidate.candidate_id}>
                        <td>
                          <strong>{candidate.candidate_id}</strong>
                          <span>{candidate.pass_type || 'unknown'}</span>
                          <span>{candidate.final_stat_eligible ? 'final eligible' : 'candidate only'}</span>
                        </td>
                        <td>
                          <strong>
                            {candidate.from_stable_player_id || 'unknown'} - {candidate.to_stable_player_id || 'unknown'}
                          </strong>
                          <span>
                            {formatTeam(candidate.from_team_name, candidate.from_team_label)}
                            {' '}to {formatTeam(candidate.to_team_name, candidate.to_team_label)}
                          </span>
                        </td>
                        <td>
                          <strong>
                            f{formatCount(candidate.start_frame)}-{formatCount(candidate.end_frame)}
                          </strong>
                          <span>
                            {formatSeconds(candidate.start_time_sec)}-{formatSeconds(candidate.end_time_sec)}s
                            {' '}({formatSeconds(candidate.duration_sec)}s)
                          </span>
                        </td>
                        <td>
                          <strong>
                            {candidate.direction || 'unknown'}
                            {candidate.is_progressive ? ' / progressive' : ''}
                          </strong>
                          <span>
                            progress {formatMeters(candidate.forward_progress_m)}
                            {' '}dist {formatMeters(candidate.distance_m)}
                            {' '}conf {formatPercent(candidate.confidence)}
                          </span>
                          <span>
                            phase {candidate.match_phase_period_id || 'n/a'} / {candidate.attack_direction || 'unknown'}
                            {' '}start {formatPoint(candidate.start_position_m)}
                            {' '}end {formatPoint(candidate.end_position_m)}
                          </span>
                        </td>
                        <td>
                          <select
                            value={draft.review_status}
                            onChange={(event) => {
                              updateDraft(candidate.candidate_id, {
                                review_status: event.target.value as PassCandidateReviewStatus
                              });
                            }}
                          >
                            {REVIEW_STATUSES.map((status) => (
                              <option key={status} value={status}>
                                {REVIEW_LABELS[status]}
                              </option>
                            ))}
                          </select>
                          <span>
                            auto: {candidate.auto_review_status || 'unknown'}
                            {' '}source: {candidate.review_source || 'unknown'}
                          </span>
                        </td>
                        <td>
                          <textarea
                            rows={2}
                            value={draft.notes}
                            placeholder='Opcjonalna uwaga z frame number'
                            onChange={(event) => {
                              updateDraft(candidate.candidate_id, { notes: event.target.value });
                            }}
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

function buildDrafts(document?: PassCandidatesDocument | null): Record<string, DraftReview> {
  const drafts: Record<string, DraftReview> = {};
  for (const candidate of document?.candidates || []) {
    drafts[candidate.candidate_id] = {
      review_status: normalizeStatus(candidate.review_status),
      notes: candidate.review_notes || ''
    };
  }
  return drafts;
}

function normalizeStatus(value: unknown): PassCandidateReviewStatus {
  if (value === 'accepted' || value === 'rejected' || value === 'uncertain' || value === 'needs_review') {
    return value;
  }
  return 'needs_review';
}

function formatTeam(name: unknown, label: unknown): string {
  return String(name || label || 'unknown team');
}

function formatPoint(value: unknown): string {
  if (!Array.isArray(value) || value.length !== 2) return '--';
  return `[${formatNumber(value[0])}, ${formatNumber(value[1])}]`;
}

function formatCount(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return String(Math.round(numeric));
}

function formatSeconds(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return numeric.toFixed(2);
}

function formatMeters(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return `${numeric.toFixed(2)}m`;
}

function formatPercent(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return `${(numeric * 100).toFixed(1)}%`;
}

function formatNumber(value: unknown): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return '--';
  return numeric.toFixed(2);
}
