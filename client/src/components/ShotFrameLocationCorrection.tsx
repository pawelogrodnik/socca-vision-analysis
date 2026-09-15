import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react';
import { frameUrl, getShotReviewFrameLocationContext, projectShotReviewFrameLocation } from '../api';
import type { ShotFrameLocationContext, ShotFrameLocationOverride, ShotFrameLocationProjection } from '../types';
import { formatKeyMomentTime } from '../lib/keyMomentTime';

type Props = {
  publishedMatchId: string;
  initialFrameTimeSec: number;
  value: ShotFrameLocationOverride | null;
  onChange: (value: ShotFrameLocationOverride | null, projection: ShotFrameLocationProjection | null) => void;
};

const timeSteps = [
  { label: '−0.5 s', delta: -0.5 },
  { label: '−0.1 s', delta: -0.1 },
  { label: '+0.1 s', delta: 0.1 },
  { label: '+0.5 s', delta: 0.5 },
];

export function ShotFrameLocationCorrection({ publishedMatchId, initialFrameTimeSec, value, onChange }: Props) {
  const [frameTimeSec, setFrameTimeSec] = useState(Math.max(0, initialFrameTimeSec));
  const [context, setContext] = useState<ShotFrameLocationContext | null>(null);
  const [loading, setLoading] = useState(true);
  const [projecting, setProjecting] = useState(false);
  const [error, setError] = useState('');
  const imageRef = useRef<HTMLImageElement>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setContext(null); setError('');
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

  return <section className='shot-frame-location-correction' aria-label='Pozycja strzału z klatki'>
    <div className='shot-frame-location-header'><strong>Pozycja z klatki</strong><span>{formatKeyMomentTime(frameTimeSec)}</span></div>
    <div className='shot-frame-location-controls' aria-label='Zmiana czasu klatki'>
      {timeSteps.slice(0, 2).map((step) => <button key={step.label} type='button' className='secondary' disabled={loading || projecting} onClick={() => moveFrame(step.delta)}>{step.label}</button>)}
      <span aria-label='Czas wybranej klatki'>{formatKeyMomentTime(frameTimeSec)}</span>
      {timeSteps.slice(2).map((step) => <button key={step.label} type='button' className='secondary' disabled={loading || projecting} onClick={() => moveFrame(step.delta)}>{step.label}</button>)}
    </div>
    {loading ? <p className='status' role='status'>Wczytuję klatkę…</p> : null}
    {context?.projection_error ? <p className='status'>{context.projection_error.detail} Nadal możesz użyć ręcznego boiska lub zapisać strzał bez pozycji.</p> : null}
    {imageSource ? <div className='shot-frame-image-wrap'>
      <img ref={imageRef} src={imageSource} alt={`Klatka meczu ${formatKeyMomentTime(frameTimeSec)}. Kliknij środek piłki.`} onClick={(event) => void selectPoint(event)} onError={() => setError('Nie udało się wczytać wybranej klatki. Nadal możesz użyć ręcznego boiska lub zapisać strzał bez pozycji.')} />
      {marker ? <span className='shot-frame-point' aria-hidden='true' style={marker} /> : null}
    </div> : null}
    {projecting ? <p className='status' role='status'>Przeliczam punkt na boisko…</p> : null}
    {value ? <div className='shot-frame-location-result'><span>Wybrano punkt z klatki.</span><button type='button' className='secondary' onClick={() => onChange(null, null)}>Wyczyść punkt</button></div> : null}
    {error ? <p className='status'>{error}</p> : null}
  </section>;
}
