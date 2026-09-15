import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { frameUrl, getShotReviewFrameLocationContext, projectShotReviewFrameLocation } from '../api';
import type { ShotFrameLocationContext, ShotFrameLocationOverride, ShotFrameLocationProjection } from '../types';
import { formatKeyMomentTime } from '../lib/keyMomentTime';

type Props = {
  publishedMatchId: string;
  initialFrameTimeSec: number;
  value: ShotFrameLocationOverride | null;
  projection: ShotFrameLocationProjection | null;
  onChange: (value: ShotFrameLocationOverride | null, projection: ShotFrameLocationProjection | null) => void;
  onClose: () => void;
};

const timeSteps = [
  { label: '−0.5 s', delta: -0.5 },
  { label: '−0.1 s', delta: -0.1 },
  { label: '+0.1 s', delta: 0.1 },
  { label: '+0.5 s', delta: 0.5 },
];

const zoomLevels = [1, 1.5, 2];

export function ShotFrameLocationCorrection({ publishedMatchId, initialFrameTimeSec, value, projection, onChange, onClose }: Props) {
  const [frameTimeSec, setFrameTimeSec] = useState(Math.max(0, value?.logical_frame_time_sec ?? initialFrameTimeSec));
  const [context, setContext] = useState<ShotFrameLocationContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [imageLoading, setImageLoading] = useState(true);
  const [projecting, setProjecting] = useState(false);
  const [error, setError] = useState('');
  const [zoomIndex, setZoomIndex] = useState(0);
  const imageRef = useRef<HTMLImageElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setImageLoading(true); setContext(null); setError('');
    void getShotReviewFrameLocationContext(publishedMatchId, frameTimeSec)
      .then((next) => { if (!cancelled) setContext(next); })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : 'Nie udało się pobrać klatki.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [publishedMatchId, frameTimeSec]);

  function moveFrame(delta: number) {
    setFrameTimeSec((current) => Math.max(0, Math.round((current + delta) * 10) / 10));
    if (value) onChange(null, null);
  }

  async function selectPoint(event: MouseEvent<HTMLImageElement>) {
    const image = event.currentTarget;
    const rect = image.getBoundingClientRect();
    if (!context || !context.projection_available || !image.naturalWidth || !image.naturalHeight || !rect.width || !rect.height) return;
    const xPx = Math.max(0, Math.min(image.naturalWidth, ((event.clientX - rect.left) / rect.width) * image.naturalWidth));
    const yPx = Math.max(0, Math.min(image.naturalHeight, ((event.clientY - rect.top) / rect.height) * image.naturalHeight));
    const next: ShotFrameLocationOverride = {
      logical_frame_time_sec: context.logical_frame_time_sec,
      x_px: Math.round(xPx * 1000) / 1000,
      y_px: Math.round(yPx * 1000) / 1000,
      frame_width: image.naturalWidth,
      frame_height: image.naturalHeight,
    };
    setProjecting(true); setError('');
    try {
      const projection = await projectShotReviewFrameLocation(publishedMatchId, next);
      onChange(next, projection);
    } catch (reason) {
      onChange(null, null);
      setError(reason instanceof Error ? reason.message : 'Nie udało się przeliczyć punktu na boisko.');
    } finally {
      setProjecting(false);
    }
  }

  const marker = value && context && value.logical_frame_time_sec === context.logical_frame_time_sec
    ? { left: `${(value.x_px / value.frame_width) * 100}%`, top: `${(value.y_px / value.frame_height) * 100}%` }
    : null;
  const imageSource = useMemo(
    () => context ? frameUrl(context.source_match_id, context.source_time_sec) : null,
    [context?.source_match_id, context?.source_time_sec],
  );

  const zoom = zoomLevels[zoomIndex];
  return <div className='shot-frame-location-overlay' role='presentation'>
    <section className='shot-frame-location-dialog' role='dialog' aria-modal='true' aria-labelledby='shot-frame-location-title'>
      <header className='shot-frame-location-header'><div><h3 id='shot-frame-location-title'>Pozycja strzału z klatki</h3><p className='muted'>Czas klatki nie zmienia czasu zapisanego strzału.</p></div><button ref={closeRef} type='button' className='secondary' onClick={onClose}>Zamknij</button></header>
      <div className='shot-frame-location-controls' aria-label='Zmiana czasu klatki'>
        {timeSteps.slice(0, 2).map((step) => <button key={step.label} type='button' className='secondary' disabled={loading || projecting} onClick={() => moveFrame(step.delta)}>{step.label}</button>)}
        <span aria-label='Czas wybranej klatki'>{formatKeyMomentTime(frameTimeSec)}</span>
        {timeSteps.slice(2).map((step) => <button key={step.label} type='button' className='secondary' disabled={loading || projecting} onClick={() => moveFrame(step.delta)}>{step.label}</button>)}
      </div>
      {(loading || imageLoading) ? <p className='status' role='status'>Wczytuję klatkę…</p> : null}
      {context?.projection_error ? <p className='status'>{context.projection_error.detail} Nadal możesz użyć ręcznego boiska lub zapisać strzał bez pozycji.</p> : null}
      {imageSource ? <div className='shot-frame-image-viewport'>
        <div className='shot-frame-image-scale' style={{ width: `${zoom * 100}%` }}>
          <img ref={imageRef} src={imageSource} alt={`Klatka meczu ${formatKeyMomentTime(frameTimeSec)}. Kliknij środek piłki.`} onLoad={() => setImageLoading(false)} onClick={(event) => void selectPoint(event)} onError={() => { setImageLoading(false); setError('Nie udało się wczytać wybranej klatki. Nadal możesz użyć ręcznego boiska lub zapisać strzał bez pozycji.'); }} />
          {marker ? <span className='shot-frame-point' aria-hidden='true' style={marker} /> : null}
        </div>
      </div> : null}
      <footer className='shot-frame-location-footer'>
        <div className='shot-frame-location-zoom' aria-label='Powiększenie klatki'><button type='button' className='secondary' disabled={zoomIndex === 0} onClick={() => setZoomIndex((index) => index - 1)}>− Zoom</button><span>{Math.round(zoom * 100)}%</span><button type='button' className='secondary' disabled={zoomIndex === zoomLevels.length - 1} onClick={() => setZoomIndex((index) => index + 1)}>+ Zoom</button></div>
        {value ? <div className='shot-frame-location-result'><span>Wybrana pozycja: {projection ? `${projection.location_m.x.toFixed(2)} × ${projection.location_m.y.toFixed(2)} m` : `${value.x_px.toFixed(0)} × ${value.y_px.toFixed(0)} px`}</span><button type='button' className='secondary' onClick={() => onChange(null, null)}>Wyczyść punkt</button></div> : null}
        {projecting ? <p className='status' role='status'>Przeliczam punkt na boisko…</p> : null}
        {error ? <p className='status'>{error}</p> : null}
        <button type='button' disabled={!value || projecting} onClick={onClose}>Zastosuj pozycję</button>
      </footer>
    </section>
  </div>;
}
