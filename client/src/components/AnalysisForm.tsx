import type { AnalysisPayload, RuntimeInfo } from '../types';

interface AnalysisFormProps {
  analysis: AnalysisPayload;
  onChange: (payload: AnalysisPayload) => void;
  runtimeInfo?: RuntimeInfo | null;
  onRun: () => Promise<void> | void;
  disabled?: boolean;
  isRunning?: boolean;
  showRunButton?: boolean;
}

function yesNo(value: boolean | undefined): string {
  return value ? 'tak' : 'nie';
}

export function AnalysisForm({
  analysis,
  onChange,
  runtimeInfo = null,
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
          <select
            value={analysis.yolo_device || ''}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, yolo_device: event.target.value || null })
            }
          >
            <option value=''>Auto</option>
            <option value='cpu'>CPU</option>
            <option value='0'>CUDA / NVIDIA GPU 0</option>
            <option value='mps'>Apple MPS</option>
          </select>
        </label>
      </div>
      {runtimeInfo && (
        <div className='runtime-box'>
          <strong>Backend runtime</strong>
          <div className='chips'>
            <span>Python {runtimeInfo.python.version}</span>
            <span>Torch {runtimeInfo.torch.available ? runtimeInfo.torch.version || 'ok' : 'brak'}</span>
            <span>CUDA: {yesNo(runtimeInfo.torch.cuda_available)}</span>
            <span>MPS: {yesNo(runtimeInfo.torch.mps_available)}</span>
          </div>
          {runtimeInfo.recommended_yolo_devices.length > 0 && (
            <p className='muted'>
              Suggested device: {runtimeInfo.recommended_yolo_devices.join(' / ')}
            </p>
          )}
        </div>
      )}
      <div className='artifact-box'>
        <label className='checkbox-row'>
          <input
            type='checkbox'
            checked={analysis.chunked}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...analysis, chunked: event.target.checked })
            }
          />
          Long video mode: zapisz chunk manifest
        </label>
        <label className='checkbox-row'>
          <input
            type='checkbox'
            checked={analysis.include_ball}
            disabled={disabled || !analysis.chunked}
            onChange={(event) =>
              onChange({ ...analysis, include_ball: event.target.checked })
            }
          />
          Analizuj pilke w tym samym chunked jobie
        </label>
        <div className='grid two compact'>
          <label>
            Chunk duration sec
            <input
              type='number'
              min={1}
              value={analysis.chunk_duration_sec}
              disabled={disabled || !analysis.chunked}
              onChange={(event) =>
                onChange({
                  ...analysis,
                  chunk_duration_sec: Number(event.target.value),
                })
              }
            />
          </label>
          <label>
            Chunk overlap sec
            <input
              type='number'
              min={0}
              value={analysis.chunk_overlap_sec}
              disabled={disabled || !analysis.chunked}
              onChange={(event) =>
                onChange({
                  ...analysis,
                  chunk_overlap_sec: Number(event.target.value),
                })
              }
            />
          </label>
        </div>
        {analysis.include_ball && (
          <div className='grid three compact'>
            <label>
              Ball model
              <input
                value={analysis.ball_yolo_model}
                disabled={disabled || !analysis.chunked}
                onChange={(event) =>
                  onChange({ ...analysis, ball_yolo_model: event.target.value })
                }
              />
            </label>
            <label>
              Ball conf
              <input
                type='number'
                step='0.01'
                value={analysis.ball_yolo_conf}
                disabled={disabled || !analysis.chunked}
                onChange={(event) =>
                  onChange({ ...analysis, ball_yolo_conf: Number(event.target.value) })
                }
              />
            </label>
            <label>
              Ball img size
              <input
                type='number'
                value={analysis.ball_yolo_imgsz}
                disabled={disabled || !analysis.chunked}
                onChange={(event) =>
                  onChange({ ...analysis, ball_yolo_imgsz: Number(event.target.value) })
                }
              />
            </label>
          </div>
        )}
        <p className='muted'>
          Chunked mode analizuje zakresy osobno, zapisuje status kazdego
          chunka i przy ponownym uruchomieniu pomija juz ukonczone chunki.
          Opcja pilki dodaje drugi model YOLO w tym samym jobie i wspolnym
          retry/resume.
        </p>
      </div>
      {showRunButton && (
        <button type='button' onClick={onRun} disabled={disabled}>
          {isRunning ? 'Analiza w toku...' : 'Uruchom analize'}
        </button>
      )}
    </div>
  );
}
