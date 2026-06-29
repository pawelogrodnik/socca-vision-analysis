import { useEffect, useMemo, useState } from 'react';
import { getContactCandidates, reviewContactCandidates } from '../api';
import type {
  ContactCandidate,
  ContactCandidateReviewStatus,
  ContactCandidateReviewUpdate,
  ContactCandidatesDocument,
  Match
} from '../types';

const REVIEW_STATUSES: ContactCandidateReviewStatus[] = [
  'needs_review',
  'accepted',
  'uncertain',
  'rejected'
];

const REVIEW_LABELS: Record<ContactCandidateReviewStatus, string> = {
  needs_review: 'Do sprawdzenia',
  accepted: 'Prawdziwy kontakt',
  uncertain: 'Niepewne',
  rejected: 'Odrzucone'
};

type DraftReview = {
  review_status: ContactCandidateReviewStatus;
  notes: string;
};

interface ContactCandidatesReviewProps {
  match: Match;
  enabled: boolean;
}

export function ContactCandidatesReview({ match, enabled }: ContactCandidatesReviewProps) {
  const [document, setDocument] = useState<ContactCandidatesDocument | null>(match.contact_candidates || null);
  const [drafts, setDrafts] = useState<Record<string, DraftReview>>(() => buildDrafts(match.contact_candidates));
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  useEffect(() => {
    let active = true;
    if (!enabled && !match.contact_candidates) {
      setDocument(null);
      setDrafts({});
      return () => {
        active = false;
      };
    }
    setLoading(true);
    setError('');
    getContactCandidates(match.id)
      .then((nextDocument) => {
        if (!active) return;
        setDocument(nextDocument);
        setDrafts(buildDrafts(nextDocument));
      })
      .catch((fetchError) => {
        if (!active) return;
        if (match.contact_candidates) {
          setDocument(match.contact_candidates);
          setDrafts(buildDrafts(match.contact_candidates));
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
  }, [enabled, match.contact_candidates, match.id]);

  const candidates = document?.candidates || [];
  const summary = document?.summary || {};
  const reviewCounts = useMemo(() => {
    const counts = { accepted: 0, needs_review: 0, rejected: 0, uncertain: 0 };
    for (const candidate of candidates) {
      const status = normalizeStatus(candidate.review_status || candidate.status);
      counts[status] += 1;
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
    const updates: ContactCandidateReviewUpdate[] = document.candidates.map((candidate) => {
      const draft = drafts[candidate.candidate_id] || {
        review_status: normalizeStatus(candidate.review_status || candidate.status),
        notes: candidate.review_notes || ''
      };
      return {
        candidate_id: candidate.candidate_id,
        review_status: draft.review_status,
        notes: draft.notes
      };
    });
    try {
      const nextDocument = await reviewContactCandidates(match.id, updates);
      setDocument(nextDocument);
      setDrafts(buildDrafts(nextDocument));
      setMessage('Zapisano review kandydatow kontaktu.');
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
          <h4>Contact candidates review</h4>
          <p className='muted'>
            Oznacz, ktore kandydaty faktycznie wygladaja jak kontakt zawodnika z pilka.
          </p>
        </div>
        <button type='button' onClick={saveReview} disabled={saving || loading || !document}>
          {saving ? 'Zapisywanie...' : 'Zapisz review'}
        </button>
      </div>
      {loading && <p className='muted'>Ladowanie kandydatow kontaktu...</p>}
      {error && <p className='error'>{error}</p>}
      {message && <p className='success'>{message}</p>}
      {document && (
        <>
          <div className='chips'>
            <span>Kandydaci: {formatCount(summary.contact_candidates ?? candidates.length)}</span>
            <span>Do sprawdzenia: {reviewCounts.needs_review}</span>
            <span>Accepted: {reviewCounts.accepted}</span>
            <span>Uncertain: {reviewCounts.uncertain}</span>
            <span>Rejected: {reviewCounts.rejected}</span>
            <span>Player interp: {formatCount(summary.candidates_with_interpolated_player_positions)}</span>
          </div>
          {candidates.length === 0 ? (
            <p className='muted'>Brak kandydatow kontaktu dla tego runu.</p>
          ) : (
            <div className='stats-table-wrap contact-review-table'>
              <table className='stats-table'>
                <thead>
                  <tr>
                    <th>Kandydat</th>
                    <th>Zawodnik</th>
                    <th>Zakres</th>
                    <th>Metryki</th>
                    <th>Review</th>
                    <th>Notatka</th>
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((candidate) => {
                    const draft = drafts[candidate.candidate_id] || {
                      review_status: normalizeStatus(candidate.review_status || candidate.status),
                      notes: candidate.review_notes || ''
                    };
                    return (
                      <tr key={candidate.candidate_id}>
                        <td>
                          <strong>{candidate.candidate_id}</strong>
                          <span>{candidate.source || 'controlled_ball_nearest_player'}</span>
                        </td>
                        <td>
                          <strong>{candidate.stable_player_id || 'unknown'}</strong>
                          <span>{formatTeam(candidate)}</span>
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
                            d={formatMeters(candidate.mean_distance_m)} min={formatMeters(candidate.min_distance_m)}
                          </strong>
                          <span>
                            conf {formatPercent(candidate.mean_confidence)}
                            {' '}ball {formatCount(candidate.detected_ball_frames)}f
                            {' '}player {formatCount(candidate.detected_player_frames)}f
                            {' '}interp {formatCount(candidate.interpolated_player_frames)}f
                          </span>
                        </td>
                        <td>
                          <select
                            value={draft.review_status}
                            onChange={(event) => {
                              updateDraft(candidate.candidate_id, {
                                review_status: event.target.value as ContactCandidateReviewStatus
                              });
                            }}
                          >
                            {REVIEW_STATUSES.map((status) => (
                              <option key={status} value={status}>
                                {REVIEW_LABELS[status]}
                              </option>
                            ))}
                          </select>
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

function buildDrafts(document?: ContactCandidatesDocument | null): Record<string, DraftReview> {
  const drafts: Record<string, DraftReview> = {};
  for (const candidate of document?.candidates || []) {
    drafts[candidate.candidate_id] = {
      review_status: normalizeStatus(candidate.review_status || candidate.status),
      notes: candidate.review_notes || ''
    };
  }
  return drafts;
}

function normalizeStatus(value: unknown): ContactCandidateReviewStatus {
  if (value === 'accepted' || value === 'rejected' || value === 'uncertain' || value === 'needs_review') {
    return value;
  }
  return 'needs_review';
}

function formatTeam(candidate: ContactCandidate): string {
  const team = candidate.team_name || candidate.team_label || 'unknown team';
  return String(team);
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
