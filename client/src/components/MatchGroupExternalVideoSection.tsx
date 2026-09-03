import { useEffect, useState } from 'react';
import type { MatchGroupExternalVideoStatus, MatchGroupVideoStatus } from '../types';

type Props = {
  groupId: string;
  localVideo: MatchGroupVideoStatus | undefined;
  externalVideo: MatchGroupExternalVideoStatus | undefined;
  busy: boolean;
  onSave: (groupId: string, url: string) => Promise<void>;
  onRemove: (groupId: string) => Promise<void>;
};

export function MatchGroupExternalVideoSection({ groupId, localVideo, externalVideo, busy, onSave, onRemove }: Props) {
  const [url, setUrl] = useState('');
  const configuredUrl = externalVideo?.external_video?.source_url || '';
  useEffect(() => { setUrl(configuredUrl); }, [configuredUrl, groupId]);
  const canSave = localVideo?.status === 'ready' && Boolean(url.trim()) && !busy;
  return <section className='external-video-settings' aria-label='Wideo YouTube'>
    <strong>Wideo YouTube</strong>
    {externalVideo?.status === 'current' && <p className='status success'>Aktywne dla obecnej wersji łącznego wideo.</p>}
    {externalVideo?.status === 'stale' && <p className='status'>Połączenie YouTube dotyczy starszej wersji wideo. Wygeneruj aktualne wideo i zapisz link ponownie.</p>}
    {externalVideo?.status === 'invalid' && <p className='status'>Zapisanego połączenia YouTube nie można bezpiecznie użyć. Ustaw poprawny link ponownie.</p>}
    <label>Link YouTube
      <input value={url} onChange={(event) => setUrl(event.target.value)} placeholder='https://www.youtube.com/watch?v=…' />
    </label>
    <div className='row'>
      <button type='button' disabled={!canSave} onClick={() => void onSave(groupId, url)}>Zapisz link YouTube</button>
      {configuredUrl && <button type='button' disabled={busy} onClick={() => void onRemove(groupId)}>Usuń link</button>}
    </div>
    {localVideo?.status !== 'ready' && <small>Link można zapisać po przygotowaniu aktualnego łącznego wideo.</small>}
  </section>;
}
