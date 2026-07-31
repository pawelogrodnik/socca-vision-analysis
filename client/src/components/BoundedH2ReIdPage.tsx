import { useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  boundedH2ArtifactUrl,
  getBoundedH2ReIdSession,
  saveBoundedH2ReIdDecision,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type {
  BoundedH2Card,
  BoundedH2Session,
} from './boundedH2ReIdTypes';

export function BoundedH2ReIdPage() {
  const { sessionId } = useParams();
  const [session, setSession] = useState<BoundedH2Session | null>(null);
  const [index, setIndex] = useState(0);
  const [status, setStatus] = useState('Ładowanie sesji…');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    void getBoundedH2ReIdSession(sessionId)
      .then((document) => {
        setSession(document);
        setStatus('');
      })
      .catch((error: unknown) => setStatus(errorMessage(error)));
  }, [sessionId]);

  const card = session?.cards[index];
  const decisions = useMemo(
    () => new Map(
      (session?.decisions || []).map((decision) => [
        decision.candidate_subject_id,
        decision,
      ]),
    ),
    [session],
  );
  const team = session?.roster.find(
    (row) => row.team_label === card?.team_label,
  );
  const cropUsesDifferentObservation = Boolean(
    card?.display_crop_observation
    && (
      card.display_crop_observation.frame !== card.frame
      || card.display_crop_observation.tracklet_id !== card.tracklet_id
    )
  );

  async function decide(
    current: BoundedH2Card,
    action: string,
    playerId?: string,
  ) {
    if (!sessionId || !session) return;
    setSaving(true);
    try {
      const next = await saveBoundedH2ReIdDecision(sessionId, {
        updates: [{
          selection_digest: session.selection_digest,
          candidate_subject_id: current.candidate_subject_id,
          observation_key: current.observation_key,
          frame: current.frame,
          tracklet_id: current.tracklet_id,
          action,
          player_id: playerId,
        }],
      });
      setSession(next);
      setIndex((value) => Math.min(value + 1, next.cards.length - 1));
      setStatus('Decyzja zapisana.');
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  async function finish() {
    if (!sessionId) return;
    setSaving(true);
    try {
      const next = await saveBoundedH2ReIdDecision(sessionId, {
        updates: [],
        finished: true,
      });
      setSession(next);
      setStatus('Sesja zakończona. Decyzje są gotowe do ewaluacji.');
    } catch (error) {
      setStatus(errorMessage(error));
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className='app bounded-h2-page'>
      <section className='hero compact-hero'>
        <p className='eyebrow'>Bounded H2 ReID evidence</p>
        <h1>5 krótkich decyzji z drugiej połowy</h1>
        <p>
          Wskaż zawodnika tylko wtedy, gdy jesteś pewien. Ranking modelu został
          zamrożony przed audytem, ale jego nazwiska pozostają ukryte do czasu
          przejścia bramki jakości.
        </p>
        <Link to='/'>Wróć do aplikacji</Link>
      </section>

      {status && <p className='status'>{status}</p>}
      {session && (
        <div className='bounded-h2-progress'>
          Karta {index + 1}/{session.cards.length} · zapisane{' '}
          {session.decisions.length}/{session.cards.length}
        </div>
      )}

      {session?.finished && (
        <p className='status'>Sesja jest zakończona i oczekuje na ewaluację.</p>
      )}

      {sessionId && card && (
        <section className='bounded-h2-layout'>
          <div className='bounded-h2-context'>
            <div className='bounded-h2-frame'>
              <img
                src={boundedH2ArtifactUrl(sessionId, card.frame_artifact)}
                alt={`Klatka ${card.frame}`}
              />
              <span
                className={`bounded-h2-box team-${card.team_label.toLowerCase()}`}
                style={{
                  left: `${100 * card.bbox_xyxy[0] / card.frame_width}%`,
                  top: `${100 * card.bbox_xyxy[1] / card.frame_height}%`,
                  width: `${100 * (card.bbox_xyxy[2] - card.bbox_xyxy[0]) / card.frame_width}%`,
                  height: `${100 * (card.bbox_xyxy[3] - card.bbox_xyxy[1]) / card.frame_height}%`,
                }}
              />
            </div>
            <div className='bounded-h2-nav'>
              <button
                type='button'
                disabled={index === 0}
                onClick={() => setIndex((value) => value - 1)}
              >
                Poprzednia
              </button>
              <button
                type='button'
                disabled={index === session.cards.length - 1}
                onClick={() => setIndex((value) => value + 1)}
              >
                Następna
              </button>
            </div>
          </div>

          <aside className='bounded-h2-actions'>
            <img
              className='bounded-h2-crop'
              src={boundedH2ArtifactUrl(sessionId, card.crop_artifact)}
              alt='Zbliżenie wybranego zawodnika'
            />
            <p>
              Team {card.team_label} · klatka {card.frame}
            </p>
            {cropUsesDifferentObservation && (
              <p className='muted'>
                Zbliżenie pochodzi z innej obserwacji tego samego zawodnika;
                decyzję podejmujesz dla ramki zaznaczonej na pełnej klatce.
              </p>
            )}
            <p className='muted'>
              Sugestie modelu są advisory-only i pozostają ukryte.
            </p>
            <div className='bounded-h2-roster'>
              {(team?.players || []).map((player) => (
                <button
                  key={player.player_id}
                  type='button'
                  disabled={saving || session.finished}
                  className={
                    decisions.get(card.candidate_subject_id)?.player_id
                    === player.player_id ? 'active' : ''
                  }
                  onClick={() => void decide(card, 'player', player.player_id)}
                >
                  {player.player_name}
                  {player.player_number ? ` #${player.player_number}` : ''}
                </button>
              ))}
            </div>
            <div className='bounded-h2-generic'>
              <button type='button' disabled={saving || session.finished} onClick={() => void decide(card, 'unknown')}>Nieznany</button>
              <button type='button' disabled={saving || session.finished} onClick={() => void decide(card, 'skip')}>Pomiń / nie wiem</button>
              <button type='button' disabled={saving || session.finished} onClick={() => void decide(card, 'bad_bbox')}>Błędny bbox</button>
              <button type='button' disabled={saving || session.finished} onClick={() => void decide(card, 'wrong_team')}>Błędny team</button>
            </div>
            <button
              type='button'
              disabled={saving || session.finished}
              onClick={() => void finish()}
            >
              Zakończ sesję
            </button>
          </aside>
        </section>
      )}
    </main>
  );
}
