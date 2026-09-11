import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { loadYouTubeIframeApi, type YouTubeIframePlayer } from '../lib/youtubeIframePlayer';
import type { KeyMomentEditorialMoment, KeyMomentEditorState, MatchGroupExternalVideoStatus, PublicMatchReport, SuggestedKeyMomentCandidate } from '../types';
import { formatReportClock } from '../lib/redesignedPublicReportPresentation';
import { AcceptedKeyMoments } from './AcceptedKeyMoments';
import { KeyMomentForm } from './KeyMomentForm';
import { SuggestedKeyMoments } from './SuggestedKeyMoments';

type Props = {
  report: PublicMatchReport;
  externalVideo: MatchGroupExternalVideoStatus | null;
  externalVideoLoading?: boolean;
  editorAllowed?: boolean;
  onEditKeyMoments?: () => void;
  editorState?: KeyMomentEditorState | null;
  onSaveEditor?: (draft: { expected_revision: string; moments: KeyMomentEditorialMoment[] }) => Promise<KeyMomentEditorState>;
  onAcceptSuggestion?: (draft: { expected_revision: string; candidate_id: string; candidate_generation_digest: string; moment: KeyMomentEditorialMoment }) => Promise<KeyMomentEditorState>;
  onRejectSuggestion?: (draft: { expected_revision: string; candidate_id: string; candidate_generation_digest: string }) => Promise<KeyMomentEditorState>;
};

type FormState = {
  mode: 'create' | 'edit' | 'accept';
  moment: KeyMomentEditorialMoment;
  expectedRevision: string;
  baseMoments: KeyMomentEditorialMoment[];
  candidate?: SuggestedKeyMomentCandidate;
  candidateGenerationDigest?: string;
};

export function youtubePlayerEmbedUrl(embedUrl: string, origin: string | null = typeof window === 'undefined' ? null : window.location.origin): string {
  const url = new URL(embedUrl);
  url.searchParams.set('enablejsapi', '1');
  if (origin) url.searchParams.set('origin', origin);
  return url.toString();
}

export function RedesignedReportVideoMoments({
  report,
  externalVideo,
  externalVideoLoading = false,
  editorState,
  onSaveEditor,
  onAcceptSuggestion,
  onRejectSuggestion,
  editorAllowed = false,
  onEditKeyMoments,
}: Props) {
  const configuredEmbedUrl = externalVideo?.external_video?.embed_url || null;
  const playerEmbedUrl = useMemo(() => configuredEmbedUrl ? youtubePlayerEmbedUrl(configuredEmbedUrl) : null, [configuredEmbedUrl]);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const playerRef = useRef<YouTubeIframePlayer | null>(null);
  const playerReadyRef = useRef(false);
  const pendingSeekRef = useRef<number | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [tab, setTab] = useState<'accepted' | 'suggested'>('accepted');
  const [form, setForm] = useState<FormState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const publicMoments = report.key_moments?.moments || [];
  const operatorEnabled = Boolean(editorState?.key_moment_editor_allowed && onSaveEditor && onAcceptSuggestion && onRejectSuggestion);
  const operatorMoments: KeyMomentEditorialMoment[] = editorState?.moments || [];

  const seekAndPlay = useCallback((timeSec: number) => {
    const player = playerRef.current;
    if (!player || !playerReadyRef.current) {
      pendingSeekRef.current = timeSec;
      return;
    }
    player.seekTo(timeSec, true);
    player.playVideo();
  }, []);

  useEffect(() => {
    if (!playerEmbedUrl || !iframeRef.current) return;
    let disposed = false;
    playerReadyRef.current = false;
    playerRef.current = null;
    void loadYouTubeIframeApi().then((api) => {
      if (disposed || !iframeRef.current) return;
      const player = new api.Player(iframeRef.current, {
        events: {
          onReady: ({ target }) => {
            if (disposed) return;
            playerRef.current = target;
            playerReadyRef.current = true;
            const pendingSeek = pendingSeekRef.current;
            pendingSeekRef.current = null;
            if (pendingSeek != null) seekAndPlay(pendingSeek);
          },
        },
      });
      playerRef.current = player;
    }).catch(() => undefined);
    return () => {
      disposed = true;
      playerReadyRef.current = false;
      playerRef.current?.destroy();
      playerRef.current = null;
    };
  }, [playerEmbedUrl, seekAndPlay]);

  useEffect(() => {
    if (!expanded) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false);
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [expanded]);

  if (!configuredEmbedUrl && !publicMoments.length && !operatorEnabled && !externalVideoLoading) return null;

  function newMoment(): KeyMomentEditorialMoment {
    const time = playerReadyRef.current ? playerRef.current?.getCurrentTime?.() : null;
    return {
      time_sec: typeof time === 'number' && Number.isFinite(time) ? time : -1,
      category: 'other',
      headline: '',
      note: '',
      team_id: null,
      player_id: null,
      origin: 'manual',
    };
  }

  function openForm(mode: FormState['mode'], moment: KeyMomentEditorialMoment, candidate?: SuggestedKeyMomentCandidate) {
    setForm({
      mode,
      moment,
      candidate,
      expectedRevision: editorState?.revision || '',
      baseMoments: operatorMoments,
      candidateGenerationDigest: editorState?.suggestions?.candidate_generation_digest || '',
    });
  }

  async function saveMoment(next: KeyMomentEditorialMoment) {
    if (!form || !editorState || !onSaveEditor || !onAcceptSuggestion) return;
    setBusy(true); setError('');
    try {
      if (form.mode === 'accept' && form.candidate) {
        const saved = await onAcceptSuggestion({
          expected_revision: form.expectedRevision,
          candidate_id: form.candidate.candidate_id,
          candidate_generation_digest: form.candidateGenerationDigest || '',
          moment: next,
        });
        if (!saved.revision) throw new Error('Serwer nie potwierdził zapisu momentu.');
        setForm(null); setTab('suggested');
      } else {
        const rows = form.mode === 'edit'
          ? form.baseMoments.map((row) => row.moment_id === next.moment_id ? next : row)
          : [next, ...form.baseMoments];
        const saved = await onSaveEditor({
          expected_revision: form.expectedRevision,
          moments: rows,
        });
        if (!saved.revision) throw new Error('Serwer nie potwierdził zapisu momentu.');
        setForm(null); setTab('accepted');
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Nie udało się zapisać momentu.');
    } finally {
      setBusy(false);
    }
  }

  async function deleteMoment(moment: KeyMomentEditorialMoment) {
    if (!editorState || !onSaveEditor || !window.confirm(`Usunąć moment „${moment.headline}”?`)) return;
    setBusy(true); setError('');
    try {
      await onSaveEditor({
        expected_revision: editorState.revision || '',
        moments: operatorMoments.filter((row) => row.moment_id !== moment.moment_id),
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Nie udało się usunąć momentu.');
    } finally {
      setBusy(false);
    }
  }

  async function rejectCandidate(candidate: SuggestedKeyMomentCandidate) {
    if (!editorState || !onRejectSuggestion) return;
    setBusy(true); setError('');
    try {
      await onRejectSuggestion({
        expected_revision: editorState.revision || '',
        candidate_id: candidate.candidate_id,
        candidate_generation_digest: editorState.suggestions?.candidate_generation_digest || '',
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Nie udało się odrzucić sugestii.');
    } finally {
      setBusy(false);
    }
  }

  return <>
    {expanded ? <div className='redesign-analysis-backdrop' aria-hidden='true' /> : null}
    <section className={`redesign-section redesign-video-section${expanded ? ' expanded' : ''}`} aria-labelledby={expanded ? undefined : 'redesign-moments-title'} role={expanded ? 'dialog' : undefined} aria-modal={expanded || undefined} aria-label={expanded ? 'Rozszerzona analiza meczu' : undefined}>
      <div className='redesign-section-heading'><div><p className='redesign-kicker'>Wideo i analiza</p><h2 id='redesign-moments-title'>Najważniejsze momenty</h2></div>
        <div className='redesign-video-actions'>{editorAllowed && !operatorEnabled && onEditKeyMoments ? <button className='redesign-quiet-button' type='button' onClick={onEditKeyMoments}>Edytuj momenty</button> : null}{configuredEmbedUrl ? <button className='redesign-quiet-button redesign-expand-analysis' type='button' data-desktop-only='true' aria-label={expanded ? 'Zwiń analizę' : 'Rozszerz analizę'} onClick={() => setExpanded((value) => !value)}><span aria-hidden='true'>{expanded ? '×' : '⛶'}</span></button> : null}</div>
      </div>
      {operatorEnabled && !form ? <div className='key-moment-operator-tabs' role='tablist' aria-label='Tryb edycji momentów'><button type='button' role='tab' aria-selected={tab === 'accepted'} onClick={() => setTab('accepted')}>Zaakceptowane {operatorMoments.length}</button><button type='button' role='tab' aria-selected={tab === 'suggested'} onClick={() => setTab('suggested')}>Sugestie {editorState?.suggestions?.unreviewed_count ?? 0}</button></div> : null}
      {externalVideoLoading ? <p className='redesign-video-loading' role='status'><span className='spinner' aria-hidden='true' /> Sprawdzam zapisane wideo YouTube…</p> : null}
      <div className={`redesign-video-layout${configuredEmbedUrl ? '' : ' moments-only'}`}>
        {playerEmbedUrl ? <div className='redesign-youtube-frame'><iframe ref={iframeRef} src={playerEmbedUrl} title='Wideo meczu na YouTube' allow='accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share' allowFullScreen /></div> : null}
        <div className='redesign-moments-panel' aria-label='Lista najważniejszych momentów'>
          {operatorEnabled ? form ? <KeyMomentForm mode={form.mode} report={report} initial={form.moment} onCancel={() => setForm(null)} onSave={saveMoment} /> : tab === 'accepted' ? <AcceptedKeyMoments moments={operatorMoments} report={report} onPlayAt={seekAndPlay} onAdd={() => openForm('create', newMoment())} onEdit={(moment) => openForm('edit', moment)} onDelete={deleteMoment} /> : <SuggestedKeyMoments state={editorState?.suggestions} report={report} disabled={busy} onPlayAt={seekAndPlay} onAccept={(candidate) => openForm('accept', { time_sec: candidate.start_time_sec, team_id: candidate.team_id || null, player_id: null, category: 'other', headline: '', note: '', origin: 'manual' }, candidate)} onReject={rejectCandidate} /> : <PublicMoments moments={publicMoments} teams={report.teams} onPlayAt={seekAndPlay} configuredEmbedUrl={Boolean(configuredEmbedUrl)} />}
          {error ? <p className='status'>{error}</p> : null}
        </div>
      </div>
    </section>
  </>;
}

function PublicMoments({
  moments,
  teams,
  onPlayAt,
  configuredEmbedUrl,
}: {
  moments: Array<{ moment_id: string; time_sec: number; headline: string; note?: string | null; team_id?: string | null }>;
  teams: PublicMatchReport['teams'];
  onPlayAt: (timeSec: number) => void;
  configuredEmbedUrl: boolean;
}) {
  const teamNames = new Map(teams.map((team) => [team.team_id, team.team_name || team.team_label || team.team_id]));
  return moments.length ? moments.map((moment) => <article className='redesign-moment-row' key={moment.moment_id}><time dateTime={`PT${Math.max(0, Math.floor(moment.time_sec))}S`}>{formatReportClock(moment.time_sec)}</time><div><h3>{moment.headline}</h3>{moment.note ? <p>{moment.note}</p> : moment.team_id ? <p>{teamNames.get(moment.team_id) || moment.team_id}</p> : null}</div>{configuredEmbedUrl ? <button type='button' className='key-moment-action-button' aria-label='Odtwórz' title='Odtwórz' onClick={() => onPlayAt(moment.time_sec)}><span aria-hidden='true'>▶</span></button> : null}</article>) : <p className='redesign-empty-state'>Brak opublikowanych momentów dla tego raportu.</p>;
}
