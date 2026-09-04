import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import {
  createMatchGroup,
  deleteMatchGroup,
  deleteMatchGroupExternalVideo,
  generateMatchGroupVideo,
  getMatchGroupExternalVideo,
  getMatchGroupVideo,
  listEligibleMatchGroupSources,
  listMatchGroups,
  previewMatchGroup,
  previewMatchGroupRefresh,
  regenerateMatchGroup,
  refreshMatchGroupToLatest,
  saveMatchGroupExternalVideo,
} from '../api';
import { errorMessage } from '../lib/helpers';
import type { MatchGroupExternalVideoStatus, MatchGroupPreview, MatchGroupRecord, MatchGroupRefreshPreview, MatchGroupSource, MatchGroupVideoStatus } from '../types';
import { MatchGroupExternalVideoSection } from './MatchGroupExternalVideoSection';

function formatDuration(value: number): string {
  const seconds = Math.max(0, Math.round(value));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
}

export function MatchGroupsPage() {
  const navigate = useNavigate();
  const [sources, setSources] = useState<MatchGroupSource[]>([]);
  const [groups, setGroups] = useState<MatchGroupRecord[]>([]);
  const [videos, setVideos] = useState<Record<string, MatchGroupVideoStatus>>({});
  const [externalVideos, setExternalVideos] = useState<Record<string, MatchGroupExternalVideoStatus>>({});
  const [refreshPreviews, setRefreshPreviews] = useState<Record<string, MatchGroupRefreshPreview>>({});
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [title, setTitle] = useState('');
  const [query, setQuery] = useState('');
  const [preview, setPreview] = useState<MatchGroupPreview | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [status, setStatus] = useState('');
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const [nextSources, nextGroups] = await Promise.all([listEligibleMatchGroupSources(), listMatchGroups()]);
    setSources(nextSources);
    setGroups(nextGroups);
    const videoRows = await Promise.all(nextGroups.map(async ({ group }) => [group.group_id, await getMatchGroupVideo(group.group_id)] as const));
    const externalRows = await Promise.all(nextGroups.map(async ({ group }) => [group.group_id, await getMatchGroupExternalVideo(group.group_id).catch(() => ({ group_id: group.group_id, status: 'not_configured' as const }))] as const));
    setVideos(Object.fromEntries(videoRows));
    setExternalVideos(Object.fromEntries(externalRows));
    const refreshRows = await Promise.all(nextGroups.map(async ({ group }) => [group.group_id, await previewMatchGroupRefresh(group.group_id).catch(() => null)] as const));
    setRefreshPreviews(Object.fromEntries(refreshRows.filter((row): row is readonly [string, MatchGroupRefreshPreview] => row[1] !== null)));
  };

  useEffect(() => { void load().catch((error: unknown) => setStatus(errorMessage(error))); }, []);

  const generatingGroupIds = useMemo(
    () => groups.map(({ group }) => group.group_id).filter((groupId) => isVideoGenerationInFlight(videos[groupId])),
    [groups, videos],
  );

  useEffect(() => {
    if (!generatingGroupIds.length) return undefined;
    let cancelled = false;
    let inFlight = false;
    let timer: number | undefined;
    const poll = async () => {
      if (inFlight || cancelled) return;
      inFlight = true;
      try {
        const rows = await Promise.all(generatingGroupIds.map(async (groupId) => [groupId, await getMatchGroupVideo(groupId)] as const));
        if (!cancelled) setVideos((current) => ({ ...current, ...Object.fromEntries(rows) }));
      } catch (error) {
        if (!cancelled) setStatus(errorMessage(error));
      } finally {
        inFlight = false;
        if (!cancelled) timer = window.setTimeout(() => void poll(), 7_500);
      }
    };
    timer = window.setTimeout(() => void poll(), 7_500);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [generatingGroupIds]);

  const selected = useMemo(
    () => selectedIds.map((id) => sources.find((source) => source.id === id)).filter((source): source is MatchGroupSource => Boolean(source)),
    [selectedIds, sources],
  );
  const duration = selected.reduce((sum, source) => sum + source.analyzed_duration_sec, 0);
  const visibleSources = sources.filter((source) => {
    const text = `${source.title} ${source.teams.join(' ')} ${source.match_date || ''}`.toLowerCase();
    return text.includes(query.toLowerCase());
  });

  useEffect(() => {
    let active = true;
    if (selectedIds.length < 2) {
      setPreview(null);
      setPreviewError('');
      return () => { active = false; };
    }
    setPreview(null);
    setPreviewError('');
    void previewMatchGroup({ member_published_ids: selectedIds, metadata: { title } })
      .then((result) => { if (active) setPreview(result); })
      .catch((error: unknown) => { if (active) setPreviewError(errorMessage(error)); });
    return () => { active = false; };
  }, [selectedIds, title]);

  const toggle = (id: string) => {
    setSelectedIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  };
  const move = (index: number, direction: -1 | 1) => {
    setSelectedIds((current) => {
      const nextIndex = index + direction;
      if (nextIndex < 0 || nextIndex >= current.length) return current;
      const next = [...current];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
  };
  const create = async () => {
    setBusy(true);
    setStatus('');
    try {
      const result = await createMatchGroup({ member_published_ids: selectedIds, metadata: { title } });
      await load();
      navigate(`/published/match-groups/${encodeURIComponent(result.group.group_id)}/report`);
    } catch (error) {
      setStatus(errorMessage(error));
    } finally { setBusy(false); }
  };
  const regenerate = async (groupId: string) => {
    setBusy(true); setStatus('');
    try { await regenerateMatchGroup(groupId); await load(); }
    catch (error) { setStatus(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const refresh = async (groupId: string) => {
    setBusy(true); setStatus('');
    try {
      const result = await refreshMatchGroupToLatest(groupId);
      setStatus(result.status === 'refreshed' ? 'Raport został odświeżony do najnowszych danych źródłowych.' : 'Dane źródłowe są aktualne.');
      await load();
    }
    catch (error) {
      // A refreshable preview can go stale before the operator confirms:
      // the POST then returns a structured 409. Reload authoritative
      // group/preview/video/external state so the stale refreshable UI is
      // replaced, but keep the exact server reason visible.
      const message = errorMessage(error);
      try { await load(); } catch { /* keep the original conflict reason */ }
      setStatus(message);
    }
    finally { setBusy(false); }
  };
  const remove = async (groupId: string) => {
    if (!window.confirm('Usunąć tylko scalony raport? Raporty źródłowe pozostaną bez zmian.')) return;
    setBusy(true); setStatus('');
    try { await deleteMatchGroup(groupId); await load(); }
    catch (error) { setStatus(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const generateVideo = async (groupId: string) => {
    setBusy(true); setStatus('');
    try {
      const nextVideo = await generateMatchGroupVideo(groupId);
      setVideos((current) => ({ ...current, [groupId]: nextVideo }));
    }
    catch (error) { setStatus(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const saveExternalVideo = async (groupId: string, url: string) => {
    setBusy(true); setStatus('');
    try {
      const next = await saveMatchGroupExternalVideo(groupId, url);
      setExternalVideos((current) => ({ ...current, [groupId]: next }));
    } catch (error) { setStatus(errorMessage(error)); }
    finally { setBusy(false); }
  };
  const removeExternalVideo = async (groupId: string) => {
    setBusy(true); setStatus('');
    try {
      const next = await deleteMatchGroupExternalVideo(groupId);
      setExternalVideos((current) => ({ ...current, [groupId]: next }));
    } catch (error) { setStatus(errorMessage(error)); }
    finally { setBusy(false); }
  };

  return <main className='app'>
    <section className='hero compact-hero'>
      <p className='eyebrow'>Opublikowane raporty</p>
      <h1>Scal opublikowane mecze</h1>
      <p>Wybierz fizyczne fragmenty, ustaw ich kolejność i utwórz oddzielny raport łączony.</p>
      <Link to='/'>Lista meczów</Link>
    </section>
    {status && <p className='status'>{status}</p>}
    <section className='panel'>
      <h2>Wybierz opublikowane fragmenty</h2>
      <label>Szukaj <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder='Tytuł, drużyna lub data' /></label>
      <div className='stack'>
        {visibleSources.map((source) => <label className='match-card' key={source.id}>
          <input type='checkbox' checked={selectedIds.includes(source.id)} onChange={() => toggle(source.id)} />
          <strong>{source.match_date || 'bez daty'} · {source.teams.join(' – ') || source.title}</strong>
          <span>{source.title} · {formatDuration(source.analyzed_duration_sec)} · {source.status}</span>
        </label>)}
        {!visibleSources.length && <p>Brak opublikowanych fizycznych raportów gotowych do scalenia.</p>}
      </div>
    </section>
    <section className='panel'>
      <h2>Wybrane fragmenty</h2>
      {selected.length < 2 && <p>Wybierz co najmniej 2 fragmenty, aby utworzyć raport łączony.</p>}
      {selected.map((source, index) => <div className='row' key={source.id}>
        <span>{index + 1}. {source.title} ({formatDuration(source.analyzed_duration_sec)})</span>
        <button type='button' disabled={index === 0} onClick={() => move(index, -1)}>Przenieś wyżej</button>
        <button type='button' disabled={index === selected.length - 1} onClick={() => move(index, 1)}>Przenieś niżej</button>
      </div>)}
      <p><strong>Łączny analizowany czas: {formatDuration(duration)}</strong></p>
      <label>Nazwa scalonego raportu <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder='Corgi - Verisk | pełny mecz' /></label>
      {selected.length >= 2 && !preview && !previewError && <p className='loading-line'>Sprawdzam zgodność na serwerze…</p>}
      {preview && <p className='status success'>Zgodne źródła. Serwer potwierdził kolejność oraz łączny czas {formatDuration(preview.timing.analyzed_duration_sec)}. Heatmapy i Team Shape pozostają niedostępne dla raportu łączonego.</p>}
      {previewError && <p className='status'>{previewError}</p>}
      <button type='button' disabled={busy || !preview || Boolean(previewError)} onClick={() => void create()}>Utwórz scalony raport</button>
    </section>
    <section className='panel'>
      <h2>Scalone raporty</h2>
      {groups.map(({ group, validation }) => <article className='match-card' key={group.group_id}>
        <strong>{group.metadata.title || group.group_id}</strong>
        <span>{formatDuration(group.timing.analyzed_duration_sec)} · {group.members.length} fragmenty · {validation.status}</span>
        {validation.blocking_reasons[0] && <p className='status'>{validation.blocking_reasons[0].detail}</p>}
        {refreshPreviews[group.group_id]?.status === 'current' && <p className='status success'>Dane źródłowe są aktualne.</p>}
        {refreshPreviews[group.group_id]?.status === 'refreshable' && <p className='status'>Dostępna jest nowsza wersja danych źródłowych. Zmienione fragmenty: {refreshPreviews[group.group_id]?.members.filter((member) => member.status === 'refreshable').length}.</p>}
        {refreshPreviews[group.group_id]?.status === 'blocked' && <p className='status'>Nie można odświeżyć: {refreshPreviews[group.group_id]?.blocking_reasons[0]?.detail || 'źródła nie są zgodne.'}</p>}
        <p>Łączne wideo: {videoLabel(videos[group.group_id])}{videoReason(videos[group.group_id]) ? ` — ${videoReason(videos[group.group_id])}` : ''}</p>
        <div className='row'>
          <Link to={`/published/match-groups/${encodeURIComponent(group.group_id)}/report`}>Otwórz raport</Link>
          <button type='button' disabled={busy || validation.status !== 'compatible'} onClick={() => void regenerate(group.group_id)}>Regeneruj raport</button>
          <button type='button' disabled={busy || isVideoGenerationInFlight(videos[group.group_id]) || refreshPreviews[group.group_id]?.status !== 'refreshable'} onClick={() => void refresh(group.group_id)}>Odśwież do najnowszych danych</button>
          {videos[group.group_id]?.status === 'ready' && videos[group.group_id]?.artifact_url && <a href={videos[group.group_id].artifact_url!}>Otwórz wideo</a>}
          <button type='button' disabled={busy || validation.status !== 'compatible' || isVideoGenerationInFlight(videos[group.group_id])} onClick={() => void generateVideo(group.group_id)}>{videos[group.group_id]?.status === 'ready' ? 'Regeneruj wideo' : 'Generuj wideo'}</button>
          <button type='button' disabled={busy || isVideoGenerationInFlight(videos[group.group_id])} onClick={() => void remove(group.group_id)}>Usuń</button>
        </div>
        <MatchGroupExternalVideoSection groupId={group.group_id} localVideo={videos[group.group_id]} externalVideo={externalVideos[group.group_id]} busy={busy} onSave={saveExternalVideo} onRemove={removeExternalVideo} />
      </article>)}
      {!groups.length && <p>Nie utworzono jeszcze scalonych raportów.</p>}
    </section>
  </main>;
}

function videoLabel(video: MatchGroupVideoStatus | undefined): string {
  if (video?.status === 'ready' && video.last_attempt?.status === 'generating') return 'Gotowe — trwa regeneracja…';
  if (video?.status === 'ready' && video.last_attempt?.status === 'failed') return 'Gotowe — ostatnia regeneracja nie powiodła się';
  return ({ not_generated: 'Nie wygenerowano', generating: 'Generowanie…', ready: 'Gotowe', stale: 'Nieaktualne', failed: 'Błąd', unavailable_source_video: 'Brak wideo źródłowego' } as const)[video?.status || 'not_generated'];
}

function videoReason(video: MatchGroupVideoStatus | undefined): string {
  const reason = video?.reason || video?.last_attempt?.reason;
  if (!reason) return '';
  return ({
    unavailable_source_video: 'jeden z opublikowanych fragmentów nie ma zweryfikowanego wideo Review',
    match_group_stale: 'źródłowy raport został zmieniony i wymaga ponownej weryfikacji',
    source_video_generation_changed: 'źródłowe wideo lub jego publikacja uległy zmianie',
    source_video_duration_mismatch: 'czas źródłowego wideo nie odpowiada czasowi fragmentu',
    video_codec_probe_failed: 'nie można bezpiecznie odczytać parametrów źródłowego wideo',
    video_generation_failed: 'nie udało się przygotować łącznego wideo; poprzednia gotowa wersja pozostaje bez zmian',
  } as Record<string, string>)[reason] || 'wymaga ponownej bezpiecznej weryfikacji';
}

function isVideoGenerationInFlight(video: MatchGroupVideoStatus | undefined): boolean {
  return video?.status === 'generating' || video?.last_attempt?.status === 'generating';
}
