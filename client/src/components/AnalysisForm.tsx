import type { AnalysisPayload } from '../types';

interface AnalysisFormProps {
  analysis: AnalysisPayload;
  onChange: (payload: AnalysisPayload) => void;
  onRun: () => Promise<void> | void;
  disabled?: boolean;
  isRunning?: boolean;
  showRunButton?: boolean;
}

export function AnalysisForm({
  analysis,
  onChange,
  onRun,
  disabled = false,
  isRunning = false,
  showRunButton = true,
}: AnalysisFormProps) {
  return (
    <div className='analysis-form'>
      <h3>Ustawienia YOLO</h3>
      <div className='grid three compact'>
        <label>
          Adapter
          <select
            value={analysis.adapter}
            disabled={disabled}
            onChange={(event) =>
              onChange({
                ...analysis,
                adapter: event.target.value as AnalysisPayload['adapter'],
              })
            }
          >
            <option value='yolo'>yolo</option>
            <option value='motion'>motion</option>
          </select>
        </label>
        <label>
          Max seconds
          <input
            type='number'
            value={analysis.max_seconds}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, max_seconds: Number(event.target.value) })
            }
          />
        </label>
        <label>
          Frame stride
          <input
            type='number'
            value={analysis.frame_stride}
            min={1}
            disabled={disabled}
            onChange={(event) =>
              onChange({
                ...analysis,
                frame_stride: Number(event.target.value),
              })
            }
          />
        </label>
      </div>
      <div className='grid three compact'>
        <label>
          Model
          <input
            value={analysis.yolo_model}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_model: event.target.value })
            }
          />
        </label>
        <label>
          Conf
          <input
            type='number'
            step='0.01'
            value={analysis.yolo_conf}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_conf: Number(event.target.value) })
            }
          />
        </label>
        <label>
          Img size
          <input
            type='number'
            value={analysis.yolo_imgsz}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_imgsz: Number(event.target.value) })
            }
          />
        </label>
      </div>
      <div className='grid two compact'>
        <label>
          Tracker
          <input
            value={analysis.yolo_tracker}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_tracker: event.target.value })
            }
          />
        </label>
        <label>
          Device
          <input
            value={analysis.yolo_device || ''}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_device: event.target.value || null })
            }
          />
        </label>
      </div>
      {showRunButton && (
        <button type='button' onClick={onRun} disabled={disabled}>
          {isRunning ? 'Analiza w toku...' : 'Uruchom analize'}
        </button>
      )}
    </div>
  );
}
