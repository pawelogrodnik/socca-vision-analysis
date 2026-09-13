import type { MouseEvent } from 'react';
import type { ShotPitchLocation } from '../types';

type Props = {
  value: ShotPitchLocation | null;
  widthM: number;
  lengthM: number;
  onChange: (point: ShotPitchLocation) => void;
};

export function ShotPitchPointSelector({ value, widthM, lengthM, onChange }: Props) {
  function select(event: MouseEvent<SVGSVGElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = Math.max(0, Math.min(widthM, ((event.clientX - rect.left) / rect.width) * widthM));
    const y = Math.max(0, Math.min(lengthM, ((event.clientY - rect.top) / rect.height) * lengthM));
    onChange({ x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 });
  }
  const markerX = value ? (value.x / widthM) * 100 : null;
  const markerY = value ? (value.y / lengthM) * 160 : null;
  return <div className='shot-pitch-selector'>
    <p className='muted'>Kliknij miejsce strzału na boisku.</p>
    <svg className='shot-pitch-canvas' role='img' aria-label='Wybór ręcznej pozycji strzału na boisku' viewBox='0 0 100 160' onClick={select}>
      <rect x='1' y='1' width='98' height='158' rx='3' className='shot-pitch-outline' />
      <line x1='1' x2='99' y1='80' y2='80' className='shot-pitch-line' />
      <circle cx='50' cy='80' r='13' className='shot-pitch-line' />
      <rect x='28' y='1' width='44' height='22' className='shot-pitch-line' />
      <rect x='28' y='137' width='44' height='22' className='shot-pitch-line' />
      {markerX != null && markerY != null ? <circle className='shot-pitch-marker' cx={markerX} cy={markerY} r='4' /> : null}
    </svg>
    {value ? <p className='muted'>Wybrano punkt ręczny.</p> : null}
  </div>;
}
