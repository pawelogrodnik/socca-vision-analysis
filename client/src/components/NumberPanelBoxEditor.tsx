import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';

export type NumberPanelBBox = [number, number, number, number];

interface NumberPanelBoxEditorProps {
  imageUrl: string;
  alt: string;
  value: NumberPanelBBox | null;
  disabled?: boolean;
  saving?: boolean;
  onChange: (value: NumberPanelBBox | null) => void;
  onSave: () => void;
  onClear: () => void;
}

type Point = {
  x: number;
  y: number;
};

function clamp(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function pointerPoint(
  event: ReactPointerEvent<HTMLDivElement>,
  element: HTMLDivElement,
): Point {
  const bounds = element.getBoundingClientRect();
  return {
    x: clamp((event.clientX - bounds.left) / bounds.width),
    y: clamp((event.clientY - bounds.top) / bounds.height),
  };
}

function bboxFromPoints(start: Point, end: Point): NumberPanelBBox {
  return [
    Math.min(start.x, end.x),
    Math.min(start.y, end.y),
    Math.max(start.x, end.x),
    Math.max(start.y, end.y),
  ];
}

export function NumberPanelBoxEditor({
  imageUrl,
  alt,
  value,
  disabled = false,
  saving = false,
  onChange,
  onSave,
  onClear,
}: NumberPanelBoxEditorProps) {
  const startRef = useRef<Point | null>(null);
  const [drawing, setDrawing] = useState(false);

  function beginDrawing(event: ReactPointerEvent<HTMLDivElement>) {
    if (disabled) return;
    const start = pointerPoint(event, event.currentTarget);
    startRef.current = start;
    setDrawing(true);
    event.currentTarget.setPointerCapture(event.pointerId);
    onChange([start.x, start.y, start.x, start.y]);
  }

  function continueDrawing(event: ReactPointerEvent<HTMLDivElement>) {
    if (!drawing || !startRef.current) return;
    onChange(bboxFromPoints(startRef.current, pointerPoint(event, event.currentTarget)));
  }

  function finishDrawing(event: ReactPointerEvent<HTMLDivElement>) {
    if (!drawing || !startRef.current) return;
    const bbox = bboxFromPoints(startRef.current, pointerPoint(event, event.currentTarget));
    const width = bbox[2] - bbox[0];
    const height = bbox[3] - bbox[1];
    onChange(width >= 0.01 && height >= 0.01 ? bbox : null);
    startRef.current = null;
    setDrawing(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  const overlayStyle = value
    ? {
        left: `${value[0] * 100}%`,
        top: `${value[1] * 100}%`,
        width: `${(value[2] - value[0]) * 100}%`,
        height: `${(value[3] - value[1]) * 100}%`,
      }
    : undefined;

  return (
    <div className='number-panel-editor'>
      <div
        className={`number-panel-editor-stage${disabled ? ' disabled' : ''}`}
        onPointerDown={beginDrawing}
        onPointerMove={continueDrawing}
        onPointerUp={finishDrawing}
        onPointerCancel={finishDrawing}
      >
        <img src={imageUrl} alt={alt} draggable={false} />
        {value && <span className='number-panel-editor-box' style={overlayStyle} />}
      </div>
      <span className='muted'>
        Przeciagnij po numerze. Gdy numeru brak, zaznacz czysty panel koszulki.
      </span>
      <div className='row number-panel-editor-actions'>
        <button
          type='button'
          className='secondary'
          onClick={onSave}
          disabled={disabled || saving || !value}
        >
          {saving ? 'Zapisywanie...' : 'Zapisz panel'}
        </button>
        <button
          type='button'
          className='secondary'
          onClick={onClear}
          disabled={disabled || saving || !value}
        >
          Wyczysc
        </button>
      </div>
    </div>
  );
}
