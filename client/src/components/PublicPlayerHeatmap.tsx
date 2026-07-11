import { useEffect, useRef } from 'react';
import type { PublicReportPlayer } from '../types';

type PublicPlayerHeatmapProps = {
  alt: string;
  fallbackSrc?: string;
  heatmap?: PublicReportPlayer['heatmap'];
};

function scaledHeatmapPoints(
  heatmap: NonNullable<PublicReportPlayer['heatmap']>['interactive'],
  width: number,
  height: number,
) {
  if (!heatmap) return [];
  const scaleX = width / Math.max(1, heatmap.width);
  const scaleY = height / Math.max(1, heatmap.height);
  return heatmap.points.map((point) => ({
    x: Math.round(point.x * scaleX),
    y: Math.round(point.y * scaleY),
    value: point.value,
  }));
}

function heatColor(intensity: number): [number, number, number, number] {
  const stops: Array<[number, number, number, number]> = [
    [0.0, 56, 189, 248],
    [0.35, 34, 197, 94],
    [0.65, 250, 204, 21],
    [1.0, 239, 68, 68],
  ];
  const clamped = Math.max(0, Math.min(1, intensity));
  for (let index = 1; index < stops.length; index += 1) {
    const previous = stops[index - 1];
    const next = stops[index];
    if (clamped <= next[0]) {
      const ratio = (clamped - previous[0]) / Math.max(0.001, next[0] - previous[0]);
      return [
        Math.round(previous[1] + (next[1] - previous[1]) * ratio),
        Math.round(previous[2] + (next[2] - previous[2]) * ratio),
        Math.round(previous[3] + (next[3] - previous[3]) * ratio),
        Math.round(255 * Math.min(0.92, 0.2 + clamped * 0.72)),
      ];
    }
  }
  return [239, 68, 68, 235];
}

export function PublicPlayerHeatmap({ alt, fallbackSrc, heatmap }: PublicPlayerHeatmapProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const interactive = heatmap?.interactive;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !interactive?.points.length) return undefined;

    let frameId = 0;
    const renderHeatmap = () => {
      frameId = 0;
      const width = canvas.clientWidth;
      const height = canvas.clientHeight;
      if (width <= 0 || height <= 0) return;
      const pixelRatio = window.devicePixelRatio || 1;
      canvas.width = Math.round(width * pixelRatio);
      canvas.height = Math.round(height * pixelRatio);
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      ctx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const alphaCanvas = document.createElement('canvas');
      alphaCanvas.width = canvas.width;
      alphaCanvas.height = canvas.height;
      const alphaCtx = alphaCanvas.getContext('2d');
      if (!alphaCtx) return;
      alphaCtx.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
      alphaCtx.clearRect(0, 0, width, height);
      alphaCtx.globalCompositeOperation = 'lighter';

      const points = scaledHeatmapPoints(interactive, width, height);
      const maxValue = Math.max(1, interactive.max_value || 1);
      const radius = Math.max(10, Math.round(interactive.radius * (width / Math.max(1, interactive.width))));
      for (const point of points) {
        const intensity = Math.min(1, point.value / maxValue);
        const gradient = alphaCtx.createRadialGradient(point.x, point.y, 0, point.x, point.y, radius);
        gradient.addColorStop(0, `rgba(0, 0, 0, ${Math.min(0.9, 0.18 + intensity * 0.72)})`);
        gradient.addColorStop(1, 'rgba(0, 0, 0, 0)');
        alphaCtx.fillStyle = gradient;
        alphaCtx.beginPath();
        alphaCtx.arc(point.x, point.y, radius, 0, Math.PI * 2);
        alphaCtx.fill();
      }

      const image = alphaCtx.getImageData(0, 0, canvas.width, canvas.height);
      const data = image.data;
      for (let index = 0; index < data.length; index += 4) {
        const intensity = data[index + 3] / 255;
        if (intensity <= 0.01) {
          data[index + 3] = 0;
          continue;
        }
        const [red, green, blue, alpha] = heatColor(intensity);
        data[index] = red;
        data[index + 1] = green;
        data[index + 2] = blue;
        data[index + 3] = alpha;
      }
      ctx.putImageData(image, 0, 0);
    };

    const scheduleRender = () => {
      if (frameId) return;
      frameId = window.requestAnimationFrame(renderHeatmap);
    };

    scheduleRender();
    const observer = new ResizeObserver(scheduleRender);
    observer.observe(canvas);

    return () => {
      observer.disconnect();
      if (frameId) window.cancelAnimationFrame(frameId);
      const ctx = canvas.getContext('2d');
      if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    };
  }, [interactive]);

  if (interactive?.points.length) {
    return (
      <div className='public-heatmap-visual' role='img' aria-label={alt}>
        <canvas className='public-heatmap-canvas' ref={canvasRef} />
        <svg className='public-heatmap-pitch-lines' viewBox='0 0 360 720' aria-hidden='true'>
          <rect x='2' y='2' width='356' height='716' />
          <line x1='2' y1='360' x2='358' y2='360' />
          <rect x='68' y='2' width='224' height='130' />
          <rect x='68' y='588' width='224' height='130' />
        </svg>
      </div>
    );
  }

  if (fallbackSrc) {
    return <img src={fallbackSrc} alt={alt} />;
  }

  return (
    <div className='player-heatmap-placeholder'>
      <span>Brak heatmapy.</span>
    </div>
  );
}
